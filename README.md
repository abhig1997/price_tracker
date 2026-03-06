# price-tracker

A self-hosted price tracker that runs on GitHub Actions and emails you when something drops. No accounts, no subscriptions, no third-party service holding your data.

---

## How it works

You keep a local `products.txt` with one `url | threshold` per line. The tracker runs every 6 hours on GitHub Actions, scrapes the price, and emails you if it hits your target. Price history accumulates in the repo over time so you have a record.

For known retailers (Amazon, Best Buy, Walmart, etc.) it auto-detects the right price element. For anything else it tries a handful of common selectors, and falls back gracefully if it can't figure it out.

The repo is designed to be public: your URLs live in a local file or GitHub Secret and are never committed. What does get committed is just price numbers and product names — nothing identifying.

---

## Setup

**1. Fork this repo** (public is fine)

**2. Create a Gmail App Password**

Regular Gmail passwords don't work for SMTP. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create one called "Price Tracker", and save the 16-character code.

**3. Add secrets** In the repo page, under Settings → Secrets and variables → Actions:

| Secret | What to put |
|---|---|
| `GMAIL_USER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-char app password |
| `ALERT_TO` | where to send alerts (can be the same address) |
| `PRODUCTS_LIST` | your products (see format below) |

**4. Format your `PRODUCTS_LIST`**

Each line is a URL and a threshold, separated by a pipe:

```
https://www.amazon.com/dp/B0XXXXXXXX | 79.99
https://www.bestbuy.com/site/some-product/123456.p | any
```

Use a number to alert when the price drops to or below that value. Use `any` to alert on any price movement at all.

**5. Enable Actions** if prompted, then trigger a first run manually from the Actions tab to make sure everything works.

---

## Running locally

Copy `products_example.txt` to `products.txt`, fill in your URLs, then:

```bash
pip install -r requirements.txt
export GMAIL_USER="you@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export ALERT_TO="you@gmail.com"
python check_prices.py
```

`products.txt` is gitignored, so it stays on your machine.

---

## Supported sites

Auto-detected out of the box: Amazon, Best Buy, Walmart, Target, Newegg, eBay, Costco, B&H Photo, Micro Center, Adorama.

For anything else, it tries common selectors like `[itemprop="price"]`, `.price`, `#price`, etc. If that fails, you can add a `price_selector` manually to `products.json` for that entry.

Amazon note: Amazon blocks scrapers aggressively and will fail intermittently. [CamelCamelCamel](https://camelcamelcamel.com) is more reliable for Amazon specifically.

---

## Files

```
products_example.txt        copy this to products.txt to get started
products.txt                your URLs — gitignored, never committed
products.json               auto-managed: name + selector cache (no URLs)
price_history.json          auto-managed: full price log (no URLs)
check_prices.py             the script
.github/workflows/
  price_tracker.yml         the Actions workflow
```

---

## Adjusting the schedule

Edit the cron in `.github/workflows/price_tracker.yml`. Default is every 6 hours. GitHub Actions free tier gives you 2,000 minutes/month — even hourly runs use about 22 minutes/day, so you have plenty of headroom.

---

## Roadmap

- Web UI for managing products and viewing price history charts
- More notification targets (Slack, Discord, SMS)
- Expanding the known-site selector table
