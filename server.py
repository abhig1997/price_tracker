#!/usr/bin/env python3
"""
Local web server for the price tracker UI.

Run with: python server.py
Then open: http://localhost:5000
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).parent
PRODUCTS_TXT = ROOT / "products.txt"
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "price_history.json"
UI_DIST = ROOT / "ui" / "dist"

app = Flask(__name__, static_folder=None)


# ── Helpers ────────────────────────────────────────────────────────────────────

def url_to_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def load_json(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def parse_products_txt() -> list[dict]:
    if not PRODUCTS_TXT.exists():
        return []
    products = []
    for line in PRODUCTS_TXT.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        url, raw = parts[0], parts[1].lower()
        threshold = "any" if raw == "any" else float(raw) if _is_float(raw) else None
        if threshold is None:
            continue
        products.append({"url": url, "threshold": threshold})
    return products


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def write_products_txt(products: list[dict]):
    lines = [
        "# Product Price Tracker - edit this file to manage your tracked products",
        "#",
        "# Format:  url | threshold",
        "#",
        "# threshold can be:",
        "#   - a price number: alert when price drops AT OR BELOW this value (e.g. 79.99)",
        '#   - "any": alert on ANY price change (up or down)',
        "#",
        "",
    ]
    for p in products:
        threshold = "any" if p["threshold"] == "any" else str(p["threshold"])
        lines.append(f"{p['url']} | {threshold}")
    PRODUCTS_TXT.write_text("\n".join(lines) + "\n")


def build_products_response() -> list[dict]:
    """Merge products.txt with history and selector status for the API response."""
    txt_products = parse_products_txt()
    stored = load_json(PRODUCTS_FILE, [])
    history = load_json(HISTORY_FILE, {})
    stored_by_id = {p["id"]: p for p in stored}

    result = []
    for p in txt_products:
        pid = url_to_id(p["url"])
        stored_entry = stored_by_id.get(pid, {})
        product_history = history.get(pid, [])
        latest = product_history[-1] if product_history else None

        result.append({
            "url": p["url"],
            "threshold": p["threshold"],
            "current_price": latest["price"] if latest else None,
            "last_checked": latest["timestamp"] if latest else None,
            "selector_status": "detected" if stored_entry.get("price_selector") else "unknown",
            "image_url": f"/api/image/{pid}" if stored_entry.get("image_url") else None,
        })

    return result


# ── API routes ─────────────────────────────────────────────────────────────────

@app.route("/api/products", methods=["GET"])
def get_products():
    return jsonify(build_products_response())


@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.get_json()
    url = (data.get("url") or "").strip()
    raw_threshold = data.get("threshold", "")

    if not url:
        return jsonify({"error": "url is required"}), 400

    if str(raw_threshold).lower() == "any":
        threshold = "any"
    else:
        try:
            threshold = float(raw_threshold)
        except (ValueError, TypeError):
            return jsonify({"error": "threshold must be a number or 'any'"}), 400

    products = parse_products_txt()
    if any(p["url"] == url for p in products):
        return jsonify({"error": "URL already tracked"}), 409

    products.append({"url": url, "threshold": threshold})
    write_products_txt(products)
    return jsonify(build_products_response()), 201


@app.route("/api/products", methods=["DELETE"])
def delete_product():
    data = request.get_json()
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    products = parse_products_txt()
    updated = [p for p in products if p["url"] != url]

    if len(updated) == len(products):
        return jsonify({"error": "URL not found"}), 404

    write_products_txt(updated)

    # Also clean up history and stored selector for the removed product
    pid = url_to_id(url)
    history = load_json(HISTORY_FILE, {})
    if pid in history:
        del history[pid]
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)

    stored = load_json(PRODUCTS_FILE, [])
    stored = [p for p in stored if p["id"] != pid]
    with open(PRODUCTS_FILE, "w") as f:
        json.dump(stored, f, indent=2)

    return jsonify(build_products_response())


@app.route("/api/history", methods=["GET"])
def get_history():
    txt_products = parse_products_txt()
    history = load_json(HISTORY_FILE, {})

    # Map ID → URL so the frontend can work with URLs directly
    id_to_url = {url_to_id(p["url"]): p["url"] for p in txt_products}
    result = {
        id_to_url[pid]: entries
        for pid, entries in history.items()
        if pid in id_to_url
    }
    return jsonify(result)


@app.route("/api/run-check", methods=["POST"])
def run_check():
    script = ROOT / "check_prices.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=120,
        )
        return jsonify({
            "ok": result.returncode == 0,
            "output": result.stdout + result.stderr,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "output": "Timed out after 120 seconds."}), 504
    except Exception as e:
        return jsonify({"ok": False, "output": str(e)}), 500


@app.route("/api/image/<pid>")
def proxy_image(pid):
    stored = load_json(PRODUCTS_FILE, [])
    entry = next((p for p in stored if p.get("id") == pid), None)
    if not entry or not entry.get("image_url"):
        return "", 404

    try:
        r = requests.get(entry["image_url"], timeout=10, stream=True)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "image/jpeg")
        return Response(r.content, content_type=content_type)
    except Exception:
        return "", 502


# ── Static file serving ────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_ui(path):
    if UI_DIST.exists():
        target = UI_DIST / path
        if path and target.exists() and target.is_file():
            return send_from_directory(UI_DIST, path)
        return send_from_directory(UI_DIST, "index.html")
    return (
        "<h2>UI not built yet.</h2>"
        "<p>Run: <code>cd ui && npm install && npm run build</code></p>"
        "<p>Or in development mode: <code>cd ui && npm run dev</code> (port 5173)</p>",
        200,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting price tracker UI at http://localhost:{port}")
    app.run(debug=True, port=port)
