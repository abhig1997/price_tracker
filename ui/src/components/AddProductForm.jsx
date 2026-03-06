import { useState } from "react";

export default function AddProductForm({ onAdded }) {
  const [url, setUrl] = useState("");
  const [threshold, setThreshold] = useState("");
  const [isAny, setIsAny] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setError("URL is required.");
      return;
    }
    if (!isAny && !threshold) {
      setError("Enter a price threshold or select 'any'.");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/products", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: trimmedUrl,
          threshold: isAny ? "any" : parseFloat(threshold),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Failed to add product.");
        return;
      }
      setUrl("");
      setThreshold("");
      setIsAny(false);
      onAdded();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form className="add-form" onSubmit={handleSubmit}>
      <h2>Track a product</h2>
      <div className="add-form-fields">
        <input
          className="url-input"
          type="url"
          placeholder="https://store.com/products/item"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
        />
        {isAny ? (
          <label className="any-toggle">
            <input
              type="checkbox"
              checked={isAny}
              onChange={(e) => setIsAny(e.target.checked)}
            />
            Any change
          </label>
        ) : (
          <>
            <input
              className="threshold-input"
              type="number"
              placeholder="Target price"
              min="0"
              step="0.01"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
            <label className="any-toggle">
              <input
                type="checkbox"
                checked={isAny}
                onChange={(e) => setIsAny(e.target.checked)}
              />
              Any change
            </label>
          </>
        )}
        <button className="add-btn" type="submit" disabled={loading}>
          {loading ? "Adding…" : "Add"}
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </form>
  );
}
