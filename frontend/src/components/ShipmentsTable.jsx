import { useEffect, useMemo, useState } from "react";
import { fetchShipments } from "../api.js";
import { titleCase, formatDestination, formatTimestamp } from "../format.js";
import StatusBadge from "./StatusBadge.jsx";
import Pagination from "./Pagination.jsx";
import ShipmentDetailDrawer from "./ShipmentDetailDrawer.jsx";
import { SearchIcon, FilterIcon, SortArrow } from "./icons.jsx";

const PAGE_SIZE = 50;

const STATUS_OPTIONS = [
  "LABEL_CREATED", "SHIPMENT_CREATED", "PACKAGE_RECEIVED", "TRACKING_ID_ISSUED",
  "IN_TRANSIT_TO_ORIGIN_HUB", "AT_DISTRIBUTION_HUB", "IN_TRANSIT", "AT_CONNECTING_HUB",
  "CUSTOMS_HOLD", "CUSTOMS_CLEARED", "IN_TRANSIT_TO_DESTINATION_HUB", "OUT_FOR_DELIVERY",
  "DELIVERED", "DELIVERY_FAILED", "RETURNED_TO_SENDER", "LOST", "CANCELLED",
];

const DELIVERY_TYPE_OPTIONS = ["STANDARD", "EXPRESS", "OVERNIGHT", "ECONOMY", "INTERNATIONAL_PRIORITY"];

const COLUMNS = [
  { key: "tracking_id", label: "Tracking ID", sortable: true },
  { key: "org_name", label: "Customer", sortable: true },
  { key: "current_status", label: "Status", sortable: false },
  { key: "delivery_type", label: "Service type", sortable: false },
  { key: "destination", label: "Destination", sortable: false },
  { key: "last_modified", label: "Last modified", sortable: true },
];

export default function ShipmentsTable() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [status, setStatus] = useState("");
  const [deliveryType, setDeliveryType] = useState("");
  const [isInternational, setIsInternational] = useState("");
  const [sortBy, setSortBy] = useState("last_modified");
  const [sortDir, setSortDir] = useState("desc");
  const [selectedTrackingId, setSelectedTrackingId] = useState(null);

  // Debounce free-text search so we don't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchShipments({ page, pageSize: PAGE_SIZE, search, status, deliveryType, isInternational, sortBy, sortDir })
      .then((data) => {
        if (cancelled) return;
        setItems(data.items);
        setTotal(data.total);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [page, search, status, deliveryType, isInternational, sortBy, sortDir]);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);

  function toggleSort(key) {
    if (sortBy === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(key);
      setSortDir("desc");
    }
    setPage(1);
  }

  function clearFilters() {
    setStatus("");
    setDeliveryType("");
    setIsInternational("");
    setPage(1);
  }

  const activeFilterCount = [status, deliveryType, isInternational].filter(Boolean).length;

  return (
    <section className="catalog-card">
      <div className="catalog-toolbar">
        <div className="catalog-toolbar__viewing">
          <span className="catalog-toolbar__viewing-label">Viewing</span>
          <span className="catalog-toolbar__viewing-count">
            {rangeStart}-{rangeEnd} <span className="catalog-toolbar__viewing-of">of</span> {total.toLocaleString("en-US")} shipments
          </span>
        </div>

        <div className="catalog-toolbar__search">
          <input
            type="text"
            placeholder="Search by tracking ID, order ID or customer"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            aria-label="Search shipments"
          />
          <SearchIcon className="catalog-toolbar__search-icon" />
        </div>

        <button
          type="button"
          className={`catalog-toolbar__filters-btn ${showFilters ? "is-open" : ""}`}
          onClick={() => setShowFilters((v) => !v)}
        >
          <FilterIcon width={16} height={16} />
          Filters
          {activeFilterCount > 0 && <span className="catalog-toolbar__filter-badge">{activeFilterCount}</span>}
        </button>
      </div>

      {showFilters && (
        <div className="filters-panel">
          <label className="filters-panel__field">
            <span>Status</span>
            <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All statuses</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{titleCase(s)}</option>
              ))}
            </select>
          </label>

          <label className="filters-panel__field">
            <span>Service type</span>
            <select value={deliveryType} onChange={(e) => { setDeliveryType(e.target.value); setPage(1); }}>
              <option value="">All service types</option>
              {DELIVERY_TYPE_OPTIONS.map((s) => (
                <option key={s} value={s}>{titleCase(s)}</option>
              ))}
            </select>
          </label>

          <label className="filters-panel__field">
            <span>Scope</span>
            <select value={isInternational} onChange={(e) => { setIsInternational(e.target.value); setPage(1); }}>
              <option value="">Domestic & international</option>
              <option value="true">International only</option>
              <option value="false">Domestic only</option>
            </select>
          </label>

          <button type="button" className="filters-panel__clear" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      )}

      <div className="catalog-table__scroll">
        <table className="catalog-table">
          <thead>
            <tr>
              {COLUMNS.map((col) => (
                <th key={col.key}>
                  {col.sortable ? (
                    <button
                      type="button"
                      className={`catalog-table__sort ${sortBy === col.key ? "is-active" : ""}`}
                      onClick={() => toggleSort(col.key)}
                    >
                      {col.label}
                      <SortArrow direction={sortBy === col.key ? sortDir : "desc"} />
                    </button>
                  ) : (
                    col.label
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={COLUMNS.length} className="catalog-table__empty">Loading shipments…</td>
              </tr>
            )}
            {!loading && error && (
              <tr>
                <td colSpan={COLUMNS.length} className="catalog-table__empty catalog-table__empty--error">
                  Couldn't load shipments: {error}
                </td>
              </tr>
            )}
            {!loading && !error && items.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="catalog-table__empty">No shipments match these filters.</td>
              </tr>
            )}
            {!loading && !error && items.map((row) => (
              <tr key={row.tracking_id}>
                <td className="catalog-table__mono">
                  <button
                    type="button"
                    className="catalog-table__link"
                    onClick={() => setSelectedTrackingId(row.tracking_id)}
                  >
                    {row.tracking_id}
                  </button>
                </td>
                <td>{row.org_name}</td>
                <td><StatusBadge status={row.current_status} /></td>
                <td>
                  {titleCase(row.delivery_type)}
                  {row.is_international && <span className="catalog-table__intl-tag">Intl</span>}
                </td>
                <td>{formatDestination(row.dest_loc)}</td>
                <td className="catalog-table__mono catalog-table__muted">{formatTimestamp(row.last_modified)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="catalog-footer">
        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>

      <ShipmentDetailDrawer
        trackingId={selectedTrackingId}
        onClose={() => setSelectedTrackingId(null)}
      />
    </section>
  );
}
