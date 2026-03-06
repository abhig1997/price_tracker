"""Integration tests for server.py Flask API endpoints."""

import json
import pytest

import server as srv
from server import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client with all data files redirected to a temp directory."""
    monkeypatch.setattr(srv, "PRODUCTS_TXT", tmp_path / "products.txt")
    monkeypatch.setattr(srv, "PRODUCTS_FILE", tmp_path / "products.json")
    monkeypatch.setattr(srv, "HISTORY_FILE", tmp_path / "price_history.json")
    # Also patch the module-level path used inside build_products_response
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── GET /api/products ──────────────────────────────────────────────────────────

class TestGetProducts:
    def test_empty_when_no_file(self, client):
        resp = client.get("/api/products")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_products(self, client, tmp_path, monkeypatch):
        (tmp_path / "products.txt").write_text(
            "https://example.com/p | 49.99\n"
        )
        resp = client.get("/api/products")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["url"] == "https://example.com/p"
        assert data[0]["threshold"] == 49.99
        assert data[0]["current_price"] is None
        assert data[0]["selector_status"] == "unknown"
        assert data[0]["image_url"] is None  # no stored image


# ── POST /api/products ─────────────────────────────────────────────────────────

class TestAddProduct:
    def test_add_product(self, client):
        resp = client.post(
            "/api/products",
            json={"url": "https://example.com/item", "threshold": 29.99},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert any(p["url"] == "https://example.com/item" for p in data)

    def test_add_product_any_threshold(self, client):
        resp = client.post(
            "/api/products",
            json={"url": "https://example.com/item", "threshold": "any"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data[0]["threshold"] == "any"

    def test_missing_url_returns_400(self, client):
        resp = client.post("/api/products", json={"threshold": 10})
        assert resp.status_code == 400

    def test_invalid_threshold_returns_400(self, client):
        resp = client.post(
            "/api/products",
            json={"url": "https://example.com/x", "threshold": "notanumber"},
        )
        assert resp.status_code == 400

    def test_duplicate_url_returns_409(self, client):
        payload = {"url": "https://example.com/dup", "threshold": 10}
        client.post("/api/products", json=payload)
        resp = client.post("/api/products", json=payload)
        assert resp.status_code == 409


# ── DELETE /api/products ───────────────────────────────────────────────────────

class TestDeleteProduct:
    def test_delete_product(self, client):
        client.post(
            "/api/products",
            json={"url": "https://example.com/del", "threshold": 5},
        )
        resp = client.delete(
            "/api/products", json={"url": "https://example.com/del"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert not any(p["url"] == "https://example.com/del" for p in data)

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete(
            "/api/products", json={"url": "https://example.com/missing"}
        )
        assert resp.status_code == 404

    def test_missing_url_returns_400(self, client):
        resp = client.delete("/api/products", json={})
        assert resp.status_code == 400

    def test_delete_cleans_history(self, client, tmp_path, monkeypatch):
        url = "https://example.com/clean"
        pid = srv.url_to_id(url)
        history = {pid: [{"timestamp": "2024-01-01T00:00:00Z", "price": 50.0}]}
        (tmp_path / "price_history.json").write_text(json.dumps(history))

        client.post("/api/products", json={"url": url, "threshold": 60})
        client.delete("/api/products", json={"url": url})

        remaining = json.loads((tmp_path / "price_history.json").read_text())
        assert pid not in remaining


# ── GET /api/history ───────────────────────────────────────────────────────────

class TestGetHistory:
    def test_empty_history(self, client):
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_returns_history_keyed_by_url(self, client, tmp_path, monkeypatch):
        url = "https://example.com/hist"
        pid = srv.url_to_id(url)
        entries = [{"timestamp": "2024-01-01T00:00:00Z", "price": 42.0}]
        (tmp_path / "products.txt").write_text(f"{url} | 50\n")
        (tmp_path / "price_history.json").write_text(json.dumps({pid: entries}))

        resp = client.get("/api/history")
        data = resp.get_json()
        assert url in data
        assert data[url][0]["price"] == 42.0



# ── GET /api/image/<pid> ───────────────────────────────────────────────────────

class TestProxyImage:
    def test_unknown_pid_returns_404(self, client):
        resp = client.get("/api/image/doesnotexist")
        assert resp.status_code == 404

    def test_pid_without_image_returns_404(self, client, tmp_path, monkeypatch):
        pid = srv.url_to_id("https://example.com/p")
        stored = [{"id": pid, "price_selector": ".price", "image_url": None}]
        (tmp_path / "products.json").write_text(json.dumps(stored))
        resp = client.get(f"/api/image/{pid}")
        assert resp.status_code == 404

    def test_image_url_in_products_response_is_opaque(self, client, tmp_path, monkeypatch):
        url = "https://example.com/item"
        pid = srv.url_to_id(url)
        (tmp_path / "products.txt").write_text(f"{url} | 50\n")
        stored = [{"id": pid, "price_selector": ".price", "image_url": "https://cdn.example.com/real-product-image.jpg"}]
        (tmp_path / "products.json").write_text(json.dumps(stored))

        resp = client.get("/api/products")
        data = resp.get_json()
        assert data[0]["image_url"] == f"/api/image/{pid}"
        assert "cdn.example.com" not in data[0]["image_url"]


    def test_excludes_orphaned_history(self, client, tmp_path, monkeypatch):
        """History entries for removed products should not appear."""
        orphan_pid = srv.url_to_id("https://example.com/gone")
        (tmp_path / "price_history.json").write_text(
            json.dumps({orphan_pid: [{"timestamp": "2024-01-01T00:00:00Z", "price": 10.0}]})
        )
        resp = client.get("/api/history")
        assert resp.get_json() == {}
