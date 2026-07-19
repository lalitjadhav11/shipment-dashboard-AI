export function titleCase(value) {
  if (!value) return "";
  return value
    .toLowerCase()
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export function formatLocation(loc) {
  if (!loc) return "—";
  const parts = [loc.address, loc.city, loc.state, loc.country_code].filter(Boolean);
  return parts.join(", ");
}

export function formatDestination(destLoc) {
  if (!destLoc) return "—";
  const parts = [destLoc.city, destLoc.state, destLoc.country_code].filter(Boolean);
  return parts.join(", ");
}

export function formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return `${date} - ${time}`;
}
