"""Unit tests for check_prices.py — all tests use synthetic HTML, no HTTP requests."""

import json

import pytest
from bs4 import BeautifulSoup

from check_prices import (
    STRUCTURED_DATA_SENTINEL,
    _detect_platform,
    _get_og_image,
    _get_product_name,
    _try_json_ld,
    _try_microdata_content,
    _try_og_meta,
    _try_text_scan,
    extract_price,
    parse_products_txt,
    products_for_storage,
    url_to_id,
)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── url_to_id ──────────────────────────────────────────────────────────────────

class TestUrlToId:
    def test_returns_12_chars(self):
        assert len(url_to_id("https://example.com/product/1")) == 12

    def test_stable(self):
        url = "https://example.com/product/1"
        assert url_to_id(url) == url_to_id(url)

    def test_different_urls_differ(self):
        assert url_to_id("https://example.com/a") != url_to_id("https://example.com/b")


# ── parse_products_txt ─────────────────────────────────────────────────────────

class TestParseProductsTxt:
    def test_basic(self, tmp_path):
        f = tmp_path / "products.txt"
        f.write_text("https://example.com/p | 49.99\n")
        import check_prices
        orig = check_prices.PRODUCTS_TXT
        check_prices.PRODUCTS_TXT = f
        try:
            result = parse_products_txt()
            assert result == [{"url": "https://example.com/p", "threshold": 49.99}]
        finally:
            check_prices.PRODUCTS_TXT = orig

    def test_any_threshold(self, tmp_path):
        f = tmp_path / "products.txt"
        f.write_text("https://example.com/p | any\n")
        import check_prices
        orig = check_prices.PRODUCTS_TXT
        check_prices.PRODUCTS_TXT = f
        try:
            result = parse_products_txt()
            assert result[0]["threshold"] == "any"
        finally:
            check_prices.PRODUCTS_TXT = orig

    def test_ignores_comments_and_blanks(self, tmp_path):
        f = tmp_path / "products.txt"
        f.write_text("# comment\n\nhttps://example.com/p | 10\n")
        import check_prices
        orig = check_prices.PRODUCTS_TXT
        check_prices.PRODUCTS_TXT = f
        try:
            result = parse_products_txt()
            assert len(result) == 1
        finally:
            check_prices.PRODUCTS_TXT = orig

    def test_skips_invalid_threshold(self, tmp_path, capsys):
        f = tmp_path / "products.txt"
        f.write_text("https://example.com/p | notanumber\n")
        import check_prices
        orig = check_prices.PRODUCTS_TXT
        check_prices.PRODUCTS_TXT = f
        try:
            result = parse_products_txt()
            assert result == []
        finally:
            check_prices.PRODUCTS_TXT = orig

    def test_missing_file_returns_empty(self, tmp_path):
        import check_prices
        orig = check_prices.PRODUCTS_TXT
        check_prices.PRODUCTS_TXT = tmp_path / "nonexistent.txt"
        try:
            assert parse_products_txt() == []
        finally:
            check_prices.PRODUCTS_TXT = orig


# ── _try_json_ld ───────────────────────────────────────────────────────────────

class TestTryJsonLd:
    def test_product_with_offers(self):
        data = {"@type": "Product", "offers": {"@type": "Offer", "price": "59.99"}}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _try_json_ld(soup(html)) == 59.99

    def test_offer_node_directly(self):
        data = {"@type": "Offer", "price": 29.0}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _try_json_ld(soup(html)) == 29.0

    def test_graph_array(self):
        data = {
            "@graph": [
                {"@type": "WebSite"},
                {"@type": "Product", "offers": {"price": "99.00"}},
            ]
        }
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _try_json_ld(soup(html)) == 99.0

    def test_list_of_nodes(self):
        data = [{"@type": "Product", "offers": {"price": "15.50"}}]
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _try_json_ld(soup(html)) == 15.50

    def test_no_price_returns_none(self):
        html = '<script type="application/ld+json">{"@type": "WebPage"}</script>'
        assert _try_json_ld(soup(html)) is None

    def test_invalid_json_skipped(self):
        html = '<script type="application/ld+json">{invalid json}</script>'
        assert _try_json_ld(soup(html)) is None

    def test_numeric_price(self):
        data = {"@type": "Product", "offers": {"price": 12}}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _try_json_ld(soup(html)) == 12.0

    def test_low_price(self):
        data = {"@type": "Product", "offers": {"lowPrice": "199.99"}}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _try_json_ld(soup(html)) == 199.99


# ── _try_og_meta ───────────────────────────────────────────────────────────────

class TestTryOgMeta:
    def test_og_price_amount(self):
        html = '<meta property="og:price:amount" content="34.95">'
        assert _try_og_meta(soup(html)) == 34.95

    def test_product_price_amount(self):
        html = '<meta property="product:price:amount" content="12.00">'
        assert _try_og_meta(soup(html)) == 12.0

    def test_european_comma_decimal(self):
        html = '<meta property="og:price:amount" content="49,00">'
        assert _try_og_meta(soup(html)) == 49.0

    def test_no_tag_returns_none(self):
        assert _try_og_meta(soup("<html></html>")) is None

    def test_empty_content_returns_none(self):
        html = '<meta property="og:price:amount" content="">'
        assert _try_og_meta(soup(html)) is None


# ── _try_microdata_content ─────────────────────────────────────────────────────

class TestTryMicrodataContent:
    def test_content_attribute(self):
        html = '<span itemprop="price" content="24.99">$24.99</span>'
        assert _try_microdata_content(soup(html)) == 24.99

    def test_text_fallback(self):
        html = '<span itemprop="price">$19.99</span>'
        assert _try_microdata_content(soup(html)) == 19.99

    def test_strips_currency_symbols(self):
        html = '<span itemprop="price" content="£39.99"></span>'
        assert _try_microdata_content(soup(html)) == 39.99

    def test_no_element_returns_none(self):
        assert _try_microdata_content(soup("<html></html>")) is None


# ── _try_text_scan ─────────────────────────────────────────────────────────────

class TestTryTextScan:
    def test_finds_dollar_price(self):
        html = '<main><div class="price">$29.99</div></main>'
        result = _try_text_scan(soup(html))
        assert result == 29.99

    def test_skips_header(self):
        html = '<header>$99.99</header>'
        assert _try_text_scan(soup(html)) is None

    def test_skips_footer(self):
        html = '<footer>$19.99</footer>'
        assert _try_text_scan(soup(html)) is None

    def test_penalizes_strikethrough(self):
        # Only a strikethrough price — low score, but still finds it
        html = '<main><del>$50.00</del></main>'
        # Should still return a value but it's the only candidate
        result = _try_text_scan(soup(html))
        assert result == 50.0

    def test_prefers_price_class(self):
        # Two prices: one in a generic div, one in .price — should pick the .price one
        html = '<main><div class="price">$39.99</div><div>$99.99</div></main>'
        result = _try_text_scan(soup(html))
        assert result == 39.99

    def test_no_price_returns_none(self):
        html = "<main><p>No prices here</p></main>"
        assert _try_text_scan(soup(html)) is None


# ── _get_product_name ─────────────────────────────────────────────────────────

class TestGetProductName:
    def test_prefers_og_title(self):
        html = '<meta property="og:title" content="Cool Sneakers"><title>Cool Sneakers | Store</title>'
        assert _get_product_name(soup(html)) == "Cool Sneakers"

    def test_falls_back_to_page_title(self):
        html = "<title>Product Page</title>"
        assert _get_product_name(soup(html)) == "Product Page"

    def test_no_title_returns_none(self):
        assert _get_product_name(soup("<html></html>")) is None

    def test_empty_og_title_falls_back_to_page_title(self):
        html = '<meta property="og:title" content=""><title>Fallback Title</title>'
        assert _get_product_name(soup(html)) == "Fallback Title"


# ── _get_og_image ──────────────────────────────────────────────────────────────

class TestGetOgImage:
    def test_finds_og_image(self):
        html = '<meta property="og:image" content="https://example.com/img.jpg">'
        assert _get_og_image(soup(html)) == "https://example.com/img.jpg"

    def test_no_tag_returns_none(self):
        assert _get_og_image(soup("<html></html>")) is None

    def test_empty_content_returns_none(self):
        html = '<meta property="og:image" content="">'
        assert _get_og_image(soup(html)) is None


# ── _detect_platform ──────────────────────────────────────────────────────────

class TestDetectPlatform:
    def test_shopify_script_src(self):
        html = '<script src="https://cdn.shopify.com/s/files/main.js"></script>'
        assert _detect_platform(soup(html)) == "shopify"

    def test_shopify_inline_script(self):
        html = "<script>var Shopify = {};</script>"
        assert _detect_platform(soup(html)) == "shopify"

    def test_woocommerce_body_class(self):
        html = '<body class="woocommerce single-product"><p>hi</p></body>'
        assert _detect_platform(soup(html)) == "woocommerce"

    def test_unknown_returns_none(self):
        html = "<html><body><p>hello</p></body></html>"
        assert _detect_platform(soup(html)) is None


# ── extract_price ──────────────────────────────────────────────────────────────

class TestExtractPrice:
    def test_css_selector(self):
        html = '<span class="price">$42.00</span>'
        assert extract_price(soup(html), ".price") == 42.0

    def test_sentinel_uses_json_ld(self):
        data = {"@type": "Offer", "price": "77.77"}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert extract_price(soup(html), STRUCTURED_DATA_SENTINEL) == 77.77

    def test_sentinel_falls_back_to_og_meta(self):
        html = '<meta property="og:price:amount" content="55.55">'
        assert extract_price(soup(html), STRUCTURED_DATA_SENTINEL) == 55.55

    def test_missing_selector_returns_none(self):
        html = "<html><body><p>nothing</p></body></html>"
        assert extract_price(soup(html), ".price") is None

    def test_parses_price_with_comma(self):
        html = '<span class="price">$1,299.00</span>'
        assert extract_price(soup(html), ".price") == 1299.0


# ── products_for_storage ──────────────────────────────────────────────────────

class TestProductsForStorage:
    def test_only_keeps_expected_fields(self):
        products = [
            {
                "id": "abc123",
                "url": "https://example.com",
                "threshold": 50.0,
                "price_selector": ".price",
                "name": "Cool Sneakers",
                "image_url": "https://example.com/img.jpg",
                "_fetch_failed": True,
            }
        ]
        result = products_for_storage(products)
        assert result == [
            {"id": "abc123", "price_selector": ".price", "name": "Cool Sneakers", "image_url": "https://example.com/img.jpg"}
        ]

    def test_image_url_none(self):
        products = [{"id": "abc123", "price_selector": ".price"}]
        result = products_for_storage(products)
        assert result[0]["image_url"] is None

    def test_name_none_when_missing(self):
        products = [{"id": "abc123", "price_selector": ".price"}]
        result = products_for_storage(products)
        assert result[0]["name"] is None
