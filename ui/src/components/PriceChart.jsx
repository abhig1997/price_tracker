import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

function formatDateShort(iso) {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const price = payload[0]?.value;
  return (
    <div style={{
      background: "#fff",
      border: "1px solid #e5e5e3",
      borderRadius: 6,
      padding: "8px 12px",
      fontSize: 12,
      boxShadow: "0 2px 8px rgba(0,0,0,0.1)",
    }}>
      <p style={{ color: "#6b7280", marginBottom: 2 }}>{label}</p>
      <p style={{ fontWeight: 600, color: "#1a1a1a" }}>${price?.toFixed(2)}</p>
    </div>
  );
}

export default function PriceChart({ history, threshold }) {
  const data = history.map((entry) => ({
    date: formatDateShort(entry.timestamp),
    price: entry.price,
  }));

  const prices = history.map((e) => e.price);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const padding = Math.max((maxPrice - minPrice) * 0.2, 2);

  const yMin = Math.max(0, Math.floor(minPrice - padding));
  const yMax = Math.ceil(maxPrice + padding);

  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0ef" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[yMin, yMax]}
          tick={{ fontSize: 11, fill: "#6b7280" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${v}`}
          width={48}
        />
        <Tooltip content={<CustomTooltip />} />
        {threshold != null && (
          <ReferenceLine
            y={threshold}
            stroke="#dc2626"
            strokeDasharray="4 4"
            strokeWidth={1.5}
            label={{
              value: `Target $${threshold.toFixed(2)}`,
              position: "insideTopRight",
              fontSize: 10,
              fill: "#dc2626",
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="price"
          stroke="#2563eb"
          strokeWidth={2}
          dot={{ r: 3, fill: "#2563eb", strokeWidth: 0 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
