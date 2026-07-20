// The nginx image proxies /api to the backend container (see nginx.conf),
// so a relative path works both in the browser and inside Docker.
// Exported so sibling service modules (e.g. agentApi.js) share one source of truth.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} -> HTTP ${res.status}`);
  }
  return res.json();
}

export function fetchSummary() {
  return getJson("/api/summary");
}

export function fetchShipmentDetail(trackingId) {
  return getJson(`/api/shipments/${encodeURIComponent(trackingId)}`);
}

export function fetchShipments({
  page = 1,
  pageSize = 50,
  search = "",
  status = "",
  deliveryType = "",
  isInternational = "",
  customsStatus = "",
  sortBy = "last_modified",
  sortDir = "desc",
} = {}) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_by: sortBy,
    sort_dir: sortDir,
  });
  if (search) params.set("search", search);
  if (status) params.set("status", status);
  if (deliveryType) params.set("delivery_type", deliveryType);
  if (isInternational) params.set("is_international", isInternational);
  if (customsStatus) params.set("customs_status", customsStatus);

  return getJson(`/api/shipments?${params.toString()}`);
}
