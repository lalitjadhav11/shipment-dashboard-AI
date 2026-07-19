// Status -> semantic tone. Tone drives a color + the label always ships with
// the dot, so meaning never rides on hue alone (dataviz status-palette rule).
const TONE_BY_STATUS = {
  LABEL_CREATED: "info",
  SHIPMENT_CREATED: "info",
  PACKAGE_RECEIVED: "info",
  TRACKING_ID_ISSUED: "info",
  IN_TRANSIT_TO_ORIGIN_HUB: "info",
  AT_DISTRIBUTION_HUB: "info",
  IN_TRANSIT: "info",
  AT_CONNECTING_HUB: "info",
  IN_TRANSIT_TO_DESTINATION_HUB: "info",
  OUT_FOR_DELIVERY: "info",
  CUSTOMS_HOLD: "warning",
  CUSTOMS_CLEARED: "good",
  DELIVERED: "good",
  DELIVERY_FAILED: "serious",
  RETURNED_TO_SENDER: "serious",
  LOST: "critical",
  CANCELLED: "critical",
};

export function statusLabel(status) {
  if (!status) return "";
  return status
    .toLowerCase()
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export default function StatusBadge({ status }) {
  const tone = TONE_BY_STATUS[status] ?? "info";
  return (
    <span className={`status-badge status-badge--${tone}`}>
      <span className="status-badge__dot" />
      {statusLabel(status)}
    </span>
  );
}
