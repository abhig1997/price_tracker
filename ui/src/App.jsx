import { useCallback, useEffect, useRef, useState } from "react";
import AddProductForm from "./components/AddProductForm";
import ProductList from "./components/ProductList";

export default function App() {
  const [products, setProducts] = useState([]);
  const [history, setHistory] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkLog, setCheckLog] = useState(null);
  const logRef = useRef(null);

  const fetchAll = useCallback(async () => {
    try {
      const [productsRes, historyRes] = await Promise.all([
        fetch("/api/products"),
        fetch("/api/history"),
      ]);
      if (!productsRes.ok || !historyRes.ok) throw new Error("Failed to load data");
      setProducts(await productsRes.json());
      setHistory(await historyRes.json());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    if (checkLog && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [checkLog]);

  async function handleRunCheck() {
    setChecking(true);
    setCheckLog("Running check_prices.py…\n");
    try {
      const res = await fetch("/api/run-check", { method: "POST" });
      const data = await res.json();
      setCheckLog(data.output || "(no output)");
      if (data.ok) await fetchAll();
    } catch {
      setCheckLog("Could not reach the server.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Price Tracker</h1>
        {products.length > 0 && (
          <span className="product-count">{products.length} product{products.length !== 1 ? "s" : ""}</span>
        )}
        <div className="header-actions">
          <button
            className="run-btn"
            onClick={handleRunCheck}
            disabled={checking}
          >
            {checking ? "Checking…" : "Run check"}
          </button>
          {checkLog && !checking && (
            <button className="log-dismiss" onClick={() => setCheckLog(null)}>✕</button>
          )}
        </div>
      </header>

      {checkLog && (
        <pre className="check-log" ref={logRef}>{checkLog}</pre>
      )}

      <main className="app-main">
        <AddProductForm onAdded={fetchAll} />

        {loading && <p className="status-msg">Loading...</p>}
        {error && <p className="status-msg error">Error: {error}. Is the server running?</p>}

        {!loading && !error && (
          <ProductList products={products} history={history} onDeleted={fetchAll} />
        )}
      </main>
    </div>
  );
}
