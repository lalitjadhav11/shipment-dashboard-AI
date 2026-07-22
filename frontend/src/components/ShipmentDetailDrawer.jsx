import { useCallback, useEffect, useRef, useState } from "react";
import { fetchShipmentDetail, fetchShipmentAiSummary, askShipmentQuestion } from "../api.js";
import { titleCase, formatLocation, formatTimestamp } from "../format.js";
import StatusBadge from "./StatusBadge.jsx";
import JourneyMap from "./JourneyMap.jsx";
import { CloseIcon, PinIcon, SparkleIcon } from "./icons.jsx";

function Field({ label, children }) {
  return (
    <div className="detail-field">
      <div className="detail-field__label">{label}</div>
      <div className="detail-field__value">{children}</div>
    </div>
  );
}

// A handful of direct, clickable starting points — mixing "about this
// shipment" and "compared to other/similar orders" so the composer isn't
// the only way in. Tailored to what's actually true of this shipment rather
// than a fixed list (e.g. no customs question for a domestic shipment).
function buildSuggestedQuestions(data) {
  if (!data) return [];
  const qs = [];
  qs.push(
    data.reason_for_delay && data.reason_for_delay !== "NONE"
      ? "Why is this shipment delayed, and when is it expected to recover?"
      : "Is this shipment on schedule?"
  );
  if (data.is_international) {
    qs.push("What's the customs status, and has that held up other shipments on this route?");
  }
  if (data.org_name) {
    qs.push(`What other shipments does ${data.org_name} have right now?`);
  }
  qs.push("How does this compare to similar shipments on the same route?");
  qs.push("What's the average delivery time for this service type?");
  return qs.slice(0, 4);
}

export default function ShipmentDetailDrawer({ trackingId, autoAiSummary, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [aiTurns, setAiTurns] = useState([]); // [{ id, query, loading, answer, error }]
  const [aiInput, setAiInput] = useState("");
  const aiAbortRef = useRef(null);
  const aiTurnIdRef = useRef(0);

  const open = Boolean(trackingId);
  const aiBusy = aiTurns.some((t) => t.loading);

  // Goes through the drawer's own /ask endpoint rather than the general
  // /api/chat pipeline — that pipeline classifies against 28 fixed templates,
  // which tends to match a narrow single-field template for a compound
  // "how does this compare to others" question and ignore the comparative
  // half entirely. This endpoint always hands the LLM the primary shipment
  // AND a sample of related shipments (same customer or same route), so it
  // can actually answer comparative follow-ups instead of just restating
  // the primary shipment's own field.
  const ask = useCallback((rawQuery) => {
    const query = rawQuery.trim();
    if (!query || !trackingId) return;

    aiAbortRef.current?.abort();
    const controller = new AbortController();
    aiAbortRef.current = controller;

    const id = ++aiTurnIdRef.current;
    setAiTurns((prev) => [...prev, { id, query, loading: true, answer: null, error: null }]);

    askShipmentQuestion(trackingId, query, { signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) return;
        setAiTurns((prev) => prev.map((t) => (t.id === id ? { ...t, loading: false, answer: detail } : t)));
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setAiTurns((prev) => prev.map((t) => (
          t.id === id ? { ...t, loading: false, error: err.message || "Couldn't get an answer." } : t
        )));
      });
  }, [trackingId]);

  // The auto-triggered opening summary uses a dedicated endpoint that hands
  // the LLM the full shipment record directly (see /api/shipments/{id}/ai-summary)
  // — the general chat pipeline's intent classifier tends to match an
  // open-ended "summarize this" question to one narrow single-field
  // template, which reads as terse rather than a real detailed summary.
  const fetchDetailedSummary = useCallback((id) => {
    if (!id) return;
    aiAbortRef.current?.abort();
    const controller = new AbortController();
    aiAbortRef.current = controller;

    const turnId = ++aiTurnIdRef.current;
    setAiTurns((prev) => [...prev, { id: turnId, query: null, loading: true, answer: null, error: null }]);

    fetchShipmentAiSummary(id, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setAiTurns((prev) => prev.map((t) => (
          t.id === turnId ? { ...t, loading: false, answer: { answer: res.summary } } : t
        )));
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setAiTurns((prev) => prev.map((t) => (
          t.id === turnId ? { ...t, loading: false, error: err.message || "Couldn't get a detailed summary." } : t
        )));
      });
  }, []);

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

  // AI summary/chat is opt-in per open (only when the diamond was clicked) so
  // plain tracking-id clicks never spend an LLM call the user didn't ask for.
  useEffect(() => {
    aiAbortRef.current?.abort();
    setAiTurns([]);
    setAiInput("");
    if (!trackingId || !autoAiSummary) return;

    fetchDetailedSummary(trackingId);

    return () => aiAbortRef.current?.abort();
  }, [trackingId, autoAiSummary, fetchDetailedSummary]);

  function handleAiSubmit(event) {
    event.preventDefault();
    ask(aiInput);
    setAiInput("");
  }

  function clearAiChat() {
    aiAbortRef.current?.abort();
    setAiTurns([]);
  }

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

              {autoAiSummary && (
                <div className="drawer-ai-summary">
                  <div className="drawer-ai-summary__header">
                    <span className="drawer-ai-summary__badge">
                      <SparkleIcon width={13} height={13} />
                    </span>
                    AI summary
                    {aiTurns.length > 0 && (
                      <button type="button" className="drawer-ai-summary__clear-btn" onClick={clearAiChat}>
                        Clear chat
                      </button>
                    )}
                  </div>

                  {aiTurns.map((turn, i) => (
                    <div className="drawer-ai-summary__turn" key={turn.id}>
                      {i > 0 && (
                        <div className="drawer-ai-summary__question">
                          <span className="drawer-ai-summary__question-label">You asked</span>
                          <span className="drawer-ai-summary__question-text">&ldquo;{turn.query}&rdquo;</span>
                        </div>
                      )}

                      {turn.loading && (
                        <div className="drawer-ai-summary__loading">
                          <span className="drawer-ai-summary__loading-dot" />
                          <span className="drawer-ai-summary__loading-dot" />
                          <span className="drawer-ai-summary__loading-dot" />
                          <span>Thinking through this shipment…</span>
                        </div>
                      )}

                      {!turn.loading && turn.error && (
                        <div className="drawer-ai-summary__error">{turn.error}</div>
                      )}

                      {!turn.loading && !turn.error && turn.answer && (
                        <>
                          <p className="drawer-ai-summary__text">{turn.answer.answer}</p>
                          {(() => {
                            // Turn 0 (the detailed summary) comes from a plain
                            // data endpoint with no suggestions of its own —
                            // offer curated, data-aware direct questions there.
                            // Later turns use the general assistant's own
                            // dynamic follow-ups.
                            const prompts = i === 0
                              ? buildSuggestedQuestions(data)
                              : (turn.answer.follow_up_suggestions || []);
                            if (prompts.length === 0) return null;
                            return (
                              <div className="drawer-ai-summary__suggestions">
                                {prompts.map((prompt) => (
                                  <button
                                    key={prompt}
                                    type="button"
                                    className="drawer-ai-summary__chip"
                                    onClick={() => ask(prompt)}
                                    disabled={aiBusy}
                                  >
                                    {prompt}
                                  </button>
                                ))}
                              </div>
                            );
                          })()}
                        </>
                      )}
                    </div>
                  ))}

                  <form className="drawer-ai-summary__composer" onSubmit={handleAiSubmit}>
                    <input
                      type="text"
                      placeholder="Ask a follow-up about this shipment…"
                      aria-label="Ask a follow-up about this shipment"
                      value={aiInput}
                      onChange={(e) => setAiInput(e.target.value)}
                      disabled={aiBusy}
                    />
                    <button type="submit" disabled={aiBusy || !aiInput.trim()}>
                      Ask
                    </button>
                  </form>
                </div>
              )}

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
