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
    ".price__current",
    ".product__price",
    "[data-product-price]",
    ".price-item",
    ".woocommerce-Price-amount",
]

# Returned by detect_selector when structured data gives a price directly (no DOM element)
STRUCTURED_DATA_SENTINEL = "__structured_data__"

# Shopify theme CSS selectors (Dawn and common third-party themes)
SHOPIFY_SELECTORS = [
    ".price__current",
    ".price-item--regular",
    ".product__price",
    "[data-product-price]",
    ".money",
]

# WooCommerce CSS selectors
WOOCOMMERCE_SELECTORS = [
    "p.price ins .woocommerce-Price-amount bdi",
    "p.price .woocommerce-Price-amount bdi",
    ".summary .price .amount",
]

MAX_PLAUSIBLE_PRICE = 50_000


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


def _get_og_image(soup: BeautifulSoup) -> str | None:
    """Extract the og:image URL from a page's Open Graph meta tags."""
    tag = soup.find("meta", {"property": "og:image"})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _try_json_ld(soup: BeautifulSoup) -> float | None:
    """Extract price from JSON-LD structured data (schema.org Product/Offer)."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Unwrap @graph arrays
        nodes = data if isinstance(data, list) else data.get("@graph", [data])

        for node in nodes:
            if not isinstance(node, dict):
                continue
            types = node.get("@type", "")
            if isinstance(types, str):
                types = [types]

            # Handle Offer nodes directly
            if "Offer" in types:
                price = node.get("price") or node.get("lowPrice")
                if price is not None:
                    try:
                        val = float(price)
                        if val > 0:
                            return val
                    except (ValueError, TypeError):
                        pass

            # Handle Product nodes — dive into offers
            if "Product" in types:
                offers = node.get("offers", {})
                if isinstance(offers, dict):
                    offers = [offers]
                for offer in offers if isinstance(offers, list) else []:
                    price = offer.get("price") or offer.get("lowPrice")
                    if price is not None:
                        try:
                            val = float(price)
                            if val > 0:
                                return val
                        except (ValueError, TypeError):
                            pass

    return None


def _try_og_meta(soup: BeautifulSoup) -> float | None:
    """Extract price from Open Graph / Facebook meta tags."""
    for prop in ("og:price:amount", "product:price:amount"):
        tag = soup.find("meta", {"property": prop})
        if tag and tag.get("content"):
            raw = tag["content"].strip()
            # Handle European decimal comma: "49,00" → "49.00"
            if "," in raw and "." not in raw and len(raw.split(",")[-1]) <= 2:
                raw = raw.replace(",", ".")
            try:
                val = float(raw.replace(",", ""))
                if val > 0:
                    return val
            except ValueError:
                pass
    return None


def _try_microdata_content(soup: BeautifulSoup) -> float | None:
    """Extract price from microdata itemprop='price', preferring the content attribute."""
    for el in soup.select("[itemprop='price']"):
        raw = el.get("content") or el.get_text(strip=True)
        if not raw:
            continue
        cleaned = raw.replace("$", "").replace(",", "").replace("£", "").replace("€", "").strip()
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            try:
                val = float(match.group())
                if val > 0:
                    return val
            except ValueError:
                pass
    return None


def _detect_platform(soup: BeautifulSoup) -> str | None:
    """Return 'shopify', 'woocommerce', or None based on page fingerprints."""
    # Shopify: check script text or asset URLs
    for script in soup.find_all("script"):
        src = script.get("src", "")
        if "cdn.shopify.com" in src:
            return "shopify"
        if script.string and "Shopify" in script.string:
            return "shopify"
    for link in soup.find_all("link", href=True):
        if "cdn.shopify.com" in link["href"]:
            return "shopify"

    # WooCommerce: body class or plugin path in any attribute
    body = soup.find("body")
    if body:
        body_classes = " ".join(body.get("class", []))
        if "woocommerce" in body_classes:
            return "woocommerce"
    for tag in soup.find_all(True):
        for attr_val in tag.attrs.values():
            if isinstance(attr_val, str) and "/woocommerce/" in attr_val:
                return "woocommerce"

    return None


def _try_platform_selectors(soup: BeautifulSoup) -> str | None:
    """Try platform-specific CSS selectors after fingerprinting the platform."""
    platform = _detect_platform(soup)
    if platform == "shopify":
        selectors = SHOPIFY_SELECTORS
    elif platform == "woocommerce":
        selectors = WOOCOMMERCE_SELECTORS
    else:
        return None

    for selector in selectors:
        if soup.select_one(selector):
            return selector
    return None


def _try_text_scan(soup: BeautifulSoup) -> float | None:
    """
    Last-resort heuristic: scan visible text for price-like patterns and score candidates.
    Excludes navigation/chrome elements to reduce false positives.
    """
    # Tags whose content we skip entirely
    skip_tags = {"header", "footer", "nav", "aside", "script", "style", "noscript"}

    price_pattern = re.compile(r'[$£€]\s*(\d{1,6}(?:[.,]\d{2})?)')

    candidates: list[tuple[float, int]] = []  # (price, score)

    for el in soup.find_all(string=price_pattern):
        # Skip if inside a blocked ancestor
        if any(p.name in skip_tags for p in el.parents):
            continue

        match = price_pattern.search(el)
        if not match:
            continue

        raw_num = match.group(1).replace(",", "")
        try:
            price = float(raw_num)
        except ValueError:
            continue

        if price <= 0 or price > MAX_PLAUSIBLE_PRICE:
            continue

        score = 0
        parent = el.parent

        # Score based on element context
        for ancestor in [parent] + list(parent.parents)[:3]:
            if not hasattr(ancestor, "get"):
                continue
            cls = " ".join(ancestor.get("class", []))
            aid = ancestor.get("id", "")
            combined = (cls + " " + aid).lower()
            if any(w in combined for w in ("price", "cost", "offer", "sale")):
                score += 3
                break

        # Bonus for being inside <main>
        if any(getattr(p, "name", "") == "main" for p in el.parents):
            score += 1

        # Penalty for strikethrough (old price)
        if any(getattr(p, "name", "") == "del" for p in el.parents):
            score -= 2

        # Penalty for <small>
        if any(getattr(p, "name", "") == "small" for p in el.parents):
            score -= 1

        # Bonus for nearby "Add to Cart" / "Buy" text
        if parent:
            nearby_text = parent.get_text(" ", strip=True).lower()
            if "add to cart" in nearby_text or "buy now" in nearby_text:
                score += 2

        candidates.append((price, score))

    if not candidates:
        return None

    best_price, _ = max(candidates, key=lambda c: c[1])
    return best_price


def detect_selector(soup: BeautifulSoup, domain: str) -> str | None:
    """Return the best price CSS selector for this page, or None if not found.

    Returns a CSS selector string, STRUCTURED_DATA_SENTINEL if a price was found
    via structured data (JSON-LD, OG meta, microdata, or text scan), or None if
    no price could be detected.
    """
    # 1. Known-site selectors
    for known_domain, selector in KNOWN_SELECTORS.items():
        if known_domain in domain:
            if soup.select_one(selector):
                return selector

    # 2. JSON-LD structured data (most reliable — covers Shopify, WooCommerce, etc.)
    if _try_json_ld(soup) is not None:
        return STRUCTURED_DATA_SENTINEL

    # 3. Open Graph / Facebook price meta tags
    if _try_og_meta(soup) is not None:
        return STRUCTURED_DATA_SENTINEL

    # 4. Microdata itemprop="price" (reads content attribute, more reliable than CSS)
    if _try_microdata_content(soup) is not None:
        return STRUCTURED_DATA_SENTINEL

    # 5. Platform-specific CSS selectors (Shopify themes, WooCommerce)
    selector = _try_platform_selectors(soup)
    if selector:
        return selector

    # 6. Generic CSS fallbacks
    for selector in FALLBACK_SELECTORS:
        if soup.select_one(selector):
            return selector

    # 7. Last-resort text scan
    if _try_text_scan(soup) is not None:
        return STRUCTURED_DATA_SENTINEL

    return None


def extract_price(soup: BeautifulSoup, selector: str) -> float | None:
    """Extract and parse a numeric price from the given CSS selector.

    If selector is STRUCTURED_DATA_SENTINEL, re-runs the structured data chain
    instead of querying the DOM (since there is no element to select).
    """
    if selector == STRUCTURED_DATA_SENTINEL:
        return (
            _try_json_ld(soup)
            or _try_og_meta(soup)
            or _try_microdata_content(soup)
            or _try_text_scan(soup)
        )

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
    Merge products.txt (source of truth for URLs + thresholds) with the
    selector cache (products.json). For new products, fetches the page once
    to detect the price selector.

    Only id and price_selector are stored — no names or URLs.
    """
    stored_by_id = {p["id"]: p for p in stored}
    result = []

    for tp in txt_products:
        pid = url_to_id(tp["url"])
        cached = stored_by_id.get(pid, {})

        product = {
            "id": pid,
            "url": tp["url"],
            "threshold": tp["threshold"],
            "price_selector": cached.get("price_selector"),
            "image_url": cached.get("image_url"),
        }

        if not product["price_selector"]:
            print(f"\nNew product — detecting price selector: {tp['url']}")
            soup, _ = fetch_page(tp["url"])
            if soup is None:
                product["_fetch_failed"] = True
            else:
                sel = detect_selector(soup, get_domain(tp["url"]))
                if sel:
                    product["price_selector"] = sel
                    print(f"  Selector: {sel}")
                else:
                    product["_detection_failed"] = True
                    print(f"  [WARN] Could not auto-detect price selector.")
                    print(f"         Add it manually: {{\"id\": \"{pid}\", \"price_selector\": \"...\"}} in products.json.")
                if not product["image_url"]:
                    product["image_url"] = _get_og_image(soup)

        result.append(product)

    return result


def products_for_storage(products: list[dict]) -> list[dict]:
    """Only id, price_selector, and image_url — no identifying information."""
    return [
        {"id": p["id"], "price_selector": p["price_selector"], "image_url": p.get("image_url")}
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

    # 3. Prune history for products no longer in products.txt
    active_ids = {p["id"] for p in products}
    history = load_json(HISTORY_FILE, {})
    removed_ids = [pid for pid in history if pid not in active_ids]
    for pid in removed_ids:
        del history[pid]
    if removed_ids:
        save_json(HISTORY_FILE, history)
        print(f"Removed history for {len(removed_ids)} deleted product(s).")

    # 4. Load email config
    email_cfg = {
        "gmail_user": os.environ.get("GMAIL_USER", ""),
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
        "alert_to": os.environ.get("ALERT_TO", ""),
    }

    alerts = []
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for product in products:
        pid = product["id"]
        url = product["url"]
        selector = product["price_selector"]
        threshold = product["threshold"]

        print(f"\nChecking: {url}")

        if not selector:
            if product.get("_fetch_failed"):
                print("  [SKIP] Site blocked or unreachable — could not detect price selector.")
            else:
                print("  [SKIP] Could not detect price selector — add 'price_selector' manually to products.json.")
            continue

        soup, title = fetch_page(url)
        if soup is None:
            print("  Skipping — could not fetch page.")
            continue

        name = title or url  # used for display and email only, never stored
        if not product.get("image_url"):
            product["image_url"] = _get_og_image(soup)
        price = extract_price(soup, selector)
        if price is None:
            print("  Skipping — could not retrieve price.")
            continue

        # Record in history — only if price changed or last entry is over a week old
        prev_entries = history.get(pid, [])
        if pid not in history:
            history[pid] = []

        should_record = True
        if prev_entries:
            last = prev_entries[-1]
            last_time = datetime.fromisoformat(last["timestamp"].rstrip("Z"))
            age_days = (datetime.utcnow() - last_time).days
            if price == last["price"] and age_days < 7:
                should_record = False

        if should_record:
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
    save_json(PRODUCTS_FILE, products_for_storage(products))
    print(f"\nHistory saved to {HISTORY_FILE}")

    if alerts and email_cfg["gmail_user"]:
        send_email_alert(email_cfg, alerts)
    elif alerts:
        print("\n[WARN] Alerts triggered but GMAIL_USER not set — skipping email.")


if __name__ == "__main__":
    main()
