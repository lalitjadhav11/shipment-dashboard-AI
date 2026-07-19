const TILES = [
  { key: "total_shipments", label: "Total shipments", tone: "neutral", format: "int" },
  { key: "on_time_pct", label: "On-time delivery", tone: "good", format: "pct" },
  { key: "in_transit_overdue", label: "Overdue in transit", tone: "warning", format: "int" },
  { key: "open_issues", label: "Open issues", tone: "serious", format: "int" },
  { key: "international_shipments", label: "International", tone: "neutral", format: "int" },
  { key: "customs_held_now", label: "Customs held", tone: "warning", format: "int" },
];

function formatValue(value, format) {
  if (value === null || value === undefined) return "—";
  if (format === "pct") return `${value}%`;
  return Number(value).toLocaleString("en-US");
}

export default function KpiRow({ summary, loading }) {
  return (
    <section className="kpi-row" aria-label="Headline shipment metrics">
      {TILES.map((tile) => (
        <div className="kpi-tile" key={tile.key}>
          <div className="kpi-tile__label">{tile.label}</div>
          <div className={`kpi-tile__value kpi-tile__value--${tile.tone}`}>
            {loading ? "…" : formatValue(summary?.[tile.key], tile.format)}
          </div>
        </div>
      ))}
    </section>
  );
}
