import { useState } from "react";
import PriceChart from "./PriceChart";

function formatPrice(price) {
  if (price == null) return null;
  return `$${price.toFixed(2)}`;
}

function formatDate(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function shortenUrl(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/$/, "");
    return u.hostname + (path.length > 40 ? path.slice(0, 40) + "…" : path);
  } catch {
    return url;
  }
}

function ProductCard({ product, history, onDeleted }) {
  const [expanded, setExpanded] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const productHistory = history[product.url] || [];
  const hasHistory = productHistory.length > 0;
  const isBelowThreshold =
    product.current_price != null &&
    product.threshold !== "any" &&
    product.current_price <= product.threshold;

  async function handleDelete(e) {
    e.stopPropagation();
    if (!confirm("Remove this product from tracking?")) return;
    setDeleting(true);
    try {
      await fetch("/api/products", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: product.url }),
      });
      onDeleted();
    } catch {
      setDeleting(false);
    }
  }

  return (
    <div className="product-card">
      <div className="product-card-header" onClick={() => setExpanded((v) => !v)}>
        {product.image_url ? (
          <img
            className="product-thumb"
            src={product.image_url}
            alt=""
            loading="lazy"
          />
        ) : (
          <span className={`status-pip ${product.selector_status}`} title={
            product.selector_status === "detected"
              ? "Price selector detected"
              : "Price selector not yet detected — run the checker first"
          } />
        )}

        <div className="product-info">
          <a
            className="product-url"
            href={product.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            title={product.url}
          >
            {shortenUrl(product.url)}
          </a>
          <div className="product-meta">
            <span className="meta-item">
              Target:{" "}
              <strong>
                {product.threshold === "any" ? "any change" : `$${Number(product.threshold).toFixed(2)}`}
              </strong>
            </span>
            {product.last_checked && (
              <span className="meta-item">Last checked: {formatDate(product.last_checked)}</span>
            )}
            {!hasHistory && (
              <span className="meta-item">No history yet — run the checker to record a price</span>
            )}
          </div>
        </div>

        {product.current_price != null && (
          <span className={`price-badge${isBelowThreshold ? " below-threshold" : ""}`}>
            {formatPrice(product.current_price)}
          </span>
        )}

        <span className={`chevron${expanded ? " open" : ""}`}>▶</span>

        <button
          className="delete-btn"
          onClick={handleDelete}
          disabled={deleting}
          title="Remove from tracking"
        >
          ✕
        </button>
      </div>

      {expanded && (
        <div className="product-chart-area">
          {hasHistory ? (
            <PriceChart
              history={productHistory}
              threshold={product.threshold !== "any" ? Number(product.threshold) : null}
            />
          ) : (
            <p className="status-msg" style={{ padding: "24px 0" }}>
              No price history yet. Run <code>python check_prices.py</code> to record the first data point.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function ProductList({ products, history, onDeleted }) {
  if (products.length === 0) {
    return (
      <p className="empty-state">
        No products tracked yet. Add a URL above to get started.
      </p>
    );
  }

  return (
    <div className="product-list">
      {products.map((p) => (
        <ProductCard
          key={p.url}
          product={p}
          history={history}
          onDeleted={onDeleted}
        />
      ))}
    </div>
  );
}
