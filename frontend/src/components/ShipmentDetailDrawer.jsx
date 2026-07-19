import { useEffect, useState } from "react";
import { fetchShipmentDetail } from "../api.js";
import { titleCase, formatLocation, formatTimestamp } from "../format.js";
import StatusBadge from "./StatusBadge.jsx";
import JourneyMap from "./JourneyMap.jsx";
import { CloseIcon, PinIcon } from "./icons.jsx";

function Field({ label, children }) {
  return (
    <div className="detail-field">
      <div className="detail-field__label">{label}</div>
      <div className="detail-field__value">{children}</div>
    </div>
  );
}

export default function ShipmentDetailDrawer({ trackingId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const open = Boolean(trackingId);

  useEffect(() => {
    if (!trackingId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);

    fetchShipmentDetail(trackingId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [trackingId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const timeline = data?.journey_timeline ? [...data.journey_timeline].reverse() : [];

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-label={`Shipment summary for ${trackingId}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer__header">
          <div>
            <div className="drawer__eyebrow">Shipment summary</div>
            <div className="drawer__tracking-id">{trackingId}</div>
          </div>
          <button type="button" className="drawer__close" onClick={onClose} aria-label="Close">
            <CloseIcon width={18} height={18} />
          </button>
        </div>

        <div className="drawer__body">
          {loading && <div className="drawer__state">Loading shipment summary…</div>}
          {!loading && error && <div className="drawer__state drawer__state--error">Couldn't load this shipment: {error}</div>}

          {!loading && !error && data && (
            <>
              <div className="drawer__status-row">
                <StatusBadge status={data.current_status} />
                {data.open_issue_count > 0 && (
                  <span className="drawer__issue-tag">{data.open_issue_count} open issue{data.open_issue_count > 1 ? "s" : ""}</span>
                )}
              </div>

              <JourneyMap detail={data} />

              <div className="detail-grid">
                <Field label="Customer">{data.org_name}</Field>
                <Field label="Order ID">{data.order_id || "—"}</Field>
                <Field label="Service type">{titleCase(data.delivery_type)}</Field>
                <Field label="Scope">{data.is_international ? "International" : "Domestic"}</Field>
                <Field label="Origin">{formatLocation(data.src_loc)}</Field>
                <Field label="Destination">{formatLocation(data.dest_loc)}</Field>
                <Field label="Customs status">{titleCase(data.customs_status)}</Field>
                <Field label="Estimated delivery">{formatTimestamp(data.estimated_delivery)}</Field>
                <Field label="Delivered">{formatTimestamp(data.delivery_date)}</Field>
              </div>

              {data.reason_for_delay && data.reason_for_delay !== "NONE" && (
                <div className="drawer__delay-callout">
                  <div className="drawer__delay-title">Delay reason: {titleCase(data.reason_for_delay)}</div>
                  {data.delay_comments && <div className="drawer__delay-comments">{data.delay_comments}</div>}
                </div>
              )}

              <div className="drawer__section-title">Journey timeline</div>
              <ol className="timeline">
                {timeline.length === 0 && <li className="timeline__empty">No tracking events yet.</li>}
                {timeline.map((ev, i) => (
                  <li className="timeline__item" key={`${ev.stage}-${ev.event_timestamp}-${i}`}>
                    <span className="timeline__dot" />
                    <div className="timeline__content">
                      <div className="timeline__stage">{titleCase(ev.stage)}</div>
                      <div className="timeline__meta">
                        {ev.location && (
                          <span className="timeline__location">
                            <PinIcon /> {ev.location}
                          </span>
                        )}
                        <span>{formatTimestamp(ev.event_timestamp)}</span>
                      </div>
                      {ev.notes && <div className="timeline__notes">{ev.notes}</div>}
                    </div>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
