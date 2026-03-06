# price-tracker

A self-hosted price tracker that runs on GitHub Actions and sends email alerts when a product hits your target price. Includes a local web UI for managing products and viewing price history charts.

---

## How it works

Add product URLs to `products.txt` with a price threshold. On a schedule, GitHub Actions fetches each page, extracts the current price, and emails you if the threshold is met. Price history accumulates in the repo over time.

The tracker automatically detects prices on most sites without any configuration. For sites it can't parse automatically, it tells you exactly what to add to fix it.

---

## Setup

**1. Fork this repo as private**

**2. Add your products**

Copy `products_example.txt` to `products.txt`. Each line is a URL and a threshold separated by a pipe:

```
https://www.somestore.com/products/item | 79.99
https://www.anotherstore.com/products/item | any
```

- Use a number to alert when the price drops **at or below** that value
- Use `any` to alert on **any price change** (up or down)

**3. Create a Gmail App Password**

Regular Gmail passwords don't work for SMTP. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create an app password called "Price Tracker", and save the 16-character code.

**4. Add repository secrets**

In your fork, go to Settings → Secrets and variables → Actions → Repository secrets and add:

| Secret | Value |
|---|---|
| `GMAIL_USER` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | The 16-character app password |
| `ALERT_TO` | Address to send alerts to (can be the same as `GMAIL_USER`) |

**5. Enable Actions and do a test run**

If prompted, enable GitHub Actions on your fork. Then trigger a manual run from the Actions tab to confirm everything is working before relying on the schedule.

---

## Web UI

A local web UI lets you manage your tracked products and view price history charts without editing files manually. It requires [Node.js](https://nodejs.org) in addition to Python.

**First-time setup:**

```bash
pip install -r requirements.txt
cd ui && npm install && npm run build && cd ..
```

**Start the UI:**

```bash
python server.py
```

Then open [http://localhost:5000](http://localhost:5000).

From the UI you can:
- Add and remove tracked products
- See current price, target, and last checked date for each product
- View a price history chart per product with a threshold reference line
- Trigger a price check run on demand and see the live output

For active development with hot reload, run `python server.py` and `cd ui && npm run dev` in separate terminals and open [http://localhost:5173](http://localhost:5173).

---

## Running the checker locally

To run a price check without the UI:

```bash
pip install -r requirements.txt
python check_prices.py
```

Email alerts are skipped if the Gmail env vars are not set — prices are printed to the console instead, which is useful for testing.

**To verify your email credentials work** before relying on real alerts, use the `--test-email` flag:

```bash
export GMAIL_USER="you@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export ALERT_TO="you@gmail.com"
python check_prices.py --test-email
```

This sends a single dummy alert email immediately, without checking any products or needing a price threshold to be met. If you see `[OK] Alert email sent`, your credentials are working. If you get an authentication error, double-check that you used an App Password (not your regular Gmail password) and that it was entered without spaces.

---

## Site compatibility

The tracker works well on most independent and D2C brand sites. It runs a multi-step detection chain to find prices automatically:

1. **JSON-LD structured data** — the most reliable method; works on virtually all Shopify stores, WooCommerce, Magento, and most D2C brand sites
2. **Open Graph meta tags** — `og:price:amount` supported by many e-commerce platforms
3. **Microdata** — `itemprop="price"` HTML attributes
4. **Platform-specific CSS selectors** — Shopify and WooCommerce theme patterns
5. **Generic CSS selectors** — common class/id patterns like `.price`, `[data-price]`, etc.
6. **Text scan** — last resort; scans visible text for currency patterns and scores candidates by context

**Sites known to block scraping:** Some large retailers (notably Amazon and Best Buy) use network-level bot protection that drops requests before any HTML is served. These sites cannot be scraped with this tool regardless of the URL or selector used. For those, use the retailer's own price alert feature, or a dedicated service like [Keepa](https://keepa.com) for Amazon.

**If auto-detection fails:** The script will print a message telling you to add a `price_selector` manually to `products.json`. To find the right selector, open the product page in Chrome, right-click the price, choose Inspect, and identify the element. You can test a selector in the browser console:

```js
document.querySelector('.your-selector')?.innerText
```

Then add it to `products.json`:

```json
{ "id": "the-id-shown-in-the-output", "price_selector": ".your-selector" }
```

---

## Adjusting the schedule

Edit the cron expression in `.github/workflows/price_tracker.yml`. The default is once per week. GitHub Actions free tier includes 2,000 minutes/month — even hourly runs use roughly 22 minutes/day, well within the free allowance.

---

## Files

```
products_example.txt          template — copy to products.txt to get started
products.txt                  your URLs and thresholds (gitignored)
products.json                 auto-managed: detected selectors and cached images (gitignored)
price_history.json            auto-managed: price history with timestamps (gitignored)
check_prices.py               the scraper and alert logic
server.py                     local web server for the UI
ui/                           React frontend (requires npm)
.github/workflows/
  price_tracker.yml           the Actions workflow
```

---

## Roadmap

- Additional notification targets (Slack, Discord, SMS)
