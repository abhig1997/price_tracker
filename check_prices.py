#!/usr/bin/env python3
"""
Price Tracker - checks product prices and sends Gmail alerts.

User configuration: edit products.txt (one "url | threshold" per line).
Internal data store: products.json (auto-managed, do not edit manually).
"""

import hashlib
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
PRODUCTS_TXT = ROOT / "products.txt"
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "price_history.json"

# ── HTTP headers ───────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Known-site selector table ──────────────────────────────────────────────────
# Maps (partial) domain → CSS selector that reliably returns the price.
KNOWN_SELECTORS = {
    "amazon.com":        "span.a-offscreen",
    "amazon.co.uk":      "span.a-offscreen",
    "amazon.ca":         "span.a-offscreen",
    "bestbuy.com":       ".priceView-customer-price span",
    "walmart.com":       "[itemprop='price']",
    "target.com":        "[data-test='product-price']",
    "newegg.com":        ".price-current",
    "ebay.com":          ".x-price-primary span",
    "costco.com":        ".your-price .value",
    "bhphotovideo.com":  "[data-selenium='pricingPrice']",
    "microcenter.com":   "#pricing",
    "adorama.com":       ".your-price",
}

# Tried in order when the site isn't in KNOWN_SELECTORS
FALLBACK_SELECTORS = [
    "[itemprop='price']",
    ".price",
    "#price",
    "[data-price]",
    ".product-price",
    ".price-box",
    ".offer-price",
    ".sale-price",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def url_to_id(url: str) -> str:
    """Stable 12-char ID derived from the URL."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def get_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def load_json(path: Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_products_txt() -> list[dict]:
    """Parse products.txt into [{url, threshold}, ...]. threshold is float or 'any'."""
    if not PRODUCTS_TXT.exists():
        return []

    products = []
    for i, line in enumerate(PRODUCTS_TXT.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            print(f"[WARN] products.txt line {i}: expected 'url | threshold', skipping.")
            continue

        url, raw = parts[0], parts[1].lower()
        try:
            threshold = "any" if raw == "any" else float(raw)
        except ValueError:
            print(f"[WARN] products.txt line {i}: invalid threshold '{parts[1]}', skipping.")
            continue

        products.append({"url": url, "threshold": threshold})

    return products


# ── Auto-detection ─────────────────────────────────────────────────────────────

def fetch_page(url: str):
    """Fetch a URL and return (BeautifulSoup, page_title). Returns (None, None) on error."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [ERROR] Could not fetch {url}: {e}")
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else urlparse(url).netloc
    return soup, title


def detect_selector(soup: BeautifulSoup, domain: str) -> str | None:
    """Return the best price CSS selector for this page, or None if not found."""
    for known_domain, selector in KNOWN_SELECTORS.items():
        if known_domain in domain:
            if soup.select_one(selector):
                return selector

    for selector in FALLBACK_SELECTORS:
        if soup.select_one(selector):
            return selector

    return None


def extract_price(soup: BeautifulSoup, selector: str) -> float | None:
    """Extract and parse a numeric price from the given CSS selector."""
    el = soup.select_one(selector)
    if not el:
        print(f"  [WARN] Selector '{selector}' not found on page.")
        return None

    raw = el.get_text(strip=True)
    cleaned = raw.replace("$", "").replace(",", "").replace("£", "").replace("€", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        print(f"  [WARN] Could not parse price from text: '{raw}'")
        return None

    return float(match.group())


# ── Product sync ───────────────────────────────────────────────────────────────

def sync_products(txt_products: list[dict], stored: list[dict]) -> list[dict]:
    """
    Merge the user's products.txt (source of truth for URLs + thresholds) with
    the internal store (cache of auto-detected name + selector).

    New products are fetched once to detect name and price selector.
    Removed products are dropped. Thresholds always follow products.txt.

    URLs are never written to products.json — only the id (URL hash), name,
    and price_selector are stored, keeping that file public-safe.
    """
    stored_by_id = {p["id"]: p for p in stored}
    result = []

    for tp in txt_products:
        pid = url_to_id(tp["url"])
        cached = stored_by_id.get(pid, {})

        product = {
            "id": pid,
            "url": tp["url"],        # runtime only — never saved to products.json
            "threshold": tp["threshold"],  # runtime only — comes from products.txt
            "name": cached.get("name"),
            "price_selector": cached.get("price_selector"),
        }

        # Only fetch the page for new products (missing name or selector)
        if not product["name"] or not product["price_selector"]:
            print(f"\nNew product detected — fetching info: {tp['url']}")
            soup, title = fetch_page(tp["url"])
            if soup:
                if not product["name"]:
                    product["name"] = title
                    print(f"  Name: {title}")
                if not product["price_selector"]:
                    sel = detect_selector(soup, get_domain(tp["url"]))
                    if sel:
                        product["price_selector"] = sel
                        print(f"  Selector: {sel}")
                    else:
                        print(f"  [WARN] Could not auto-detect price selector.")
                        print(f"         Add 'price_selector' manually to products.json for this product.")

        result.append(product)

    return result


def products_for_storage(products: list[dict]) -> list[dict]:
    """Strip runtime-only fields before saving to products.json."""
    return [
        {"id": p["id"], "name": p["name"], "price_selector": p["price_selector"]}
        for p in products
    ]


# ── Email alert ────────────────────────────────────────────────────────────────

def send_email_alert(cfg: dict, alerts: list[dict]):
    """Send a single digest email covering all triggered alerts."""
    gmail_user = cfg["gmail_user"]
    gmail_password = cfg["gmail_app_password"]
    to_address = cfg.get("alert_to", gmail_user)

    subject = f"Price Alert: {len(alerts)} product(s) need your attention"

    lines = ["The following products triggered a price alert:\n"]
    for a in alerts:
        lines.append(f"Product : {a['name']}")
        lines.append(f"Current : ${a['current_price']:.2f}")
        if a.get("threshold") == "any":
            change = a["price_change"]
            direction = "down" if change < 0 else "up"
            lines.append(f"Change  : {direction} ${abs(change):.2f} from ${a['previous_price']:.2f}")
        else:
            lines.append(f"Target  : ${a['threshold']:.2f}")
        lines.append(f"URL     : {a['url']}")
        lines.append("")

    body = "\n".join(lines)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = to_address
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_address, msg.as_string())
        print(f"  [OK] Alert email sent to {to_address}")
    except Exception as e:
        print(f"  [ERROR] Failed to send email: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # 1. Parse products.txt
    txt_products = parse_products_txt()
    if not txt_products:
        print("No products in products.txt. Add a URL and threshold to get started.")
        sys.exit(0)

    # 2. Sync with internal store (auto-detects name/selector for new entries)
    stored = load_json(PRODUCTS_FILE, [])
    products = sync_products(txt_products, stored)
    save_json(PRODUCTS_FILE, products_for_storage(products))

    # 3. Load history + email config
    history = load_json(HISTORY_FILE, {})
    email_cfg = {
        "gmail_user": os.environ.get("GMAIL_USER", ""),
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
        "alert_to": os.environ.get("ALERT_TO", ""),
    }

    alerts = []
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for product in products:
        pid = product["id"]
        name = product["name"] or product["url"]
        url = product["url"]
        selector = product["price_selector"]
        threshold = product["threshold"]

        print(f"\nChecking: {name}")

        if not selector:
            print("  [SKIP] No price selector found — add 'price_selector' manually to products.json.")
            continue

        soup, _ = fetch_page(url)
        if soup is None:
            print("  Skipping — could not fetch page.")
            continue

        price = extract_price(soup, selector)
        if price is None:
            print("  Skipping — could not retrieve price.")
            continue

        # Record in history
        prev_entries = history.get(pid, [])
        if pid not in history:
            history[pid] = []
        history[pid].append({"timestamp": timestamp, "price": price})

        # Alert logic
        if threshold == "any":
            if prev_entries:
                last_price = prev_entries[-1]["price"]
                change = price - last_price
                if change != 0:
                    direction = "down" if change < 0 else "up"
                    print(f"  Price: ${price:.2f}  |  Last: ${last_price:.2f}  ({direction} ${abs(change):.2f})")
                    print(f"  Price changed — queuing alert.")
                    alerts.append({
                        "name": name,
                        "url": url,
                        "current_price": price,
                        "previous_price": last_price,
                        "price_change": change,
                        "threshold": "any",
                    })
                else:
                    print(f"  Price: ${price:.2f}  |  No change.")
            else:
                print(f"  Price: ${price:.2f}  |  First check — recording baseline.")
        else:
            print(f"  Price: ${price:.2f}  |  Threshold: ${threshold:.2f}")
            if price <= threshold:
                print(f"  Below threshold — queuing alert.")
                alerts.append({
                    "name": name,
                    "url": url,
                    "current_price": price,
                    "threshold": threshold,
                })

    save_json(HISTORY_FILE, history)
    print(f"\nHistory saved to {HISTORY_FILE}")

    if alerts and email_cfg["gmail_user"]:
        send_email_alert(email_cfg, alerts)
    elif alerts:
        print("\n[WARN] Alerts triggered but GMAIL_USER not set — skipping email.")


if __name__ == "__main__":
    main()
