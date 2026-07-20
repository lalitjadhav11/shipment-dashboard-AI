"""
Stage 7 v0 — Template Response Formatter (no LLM). Deterministic, instant,
and covers exactly the intents Stage 4a knows how to query for. v1 replaces
this module with an LLM tool-call against the same ShipmentAnswer contract
(see AGENTIC_RAG_ARCHITECTURE.md §4 Stage 7 v1) — everything downstream of
this stage doesn't need to know which version produced the answer.
"""
from pydantic import BaseModel


class ShipmentAnswer(BaseModel):
    answer: str
    tracking_id: str | None = None
    current_status: str | None = None
    confidence_score: float
    supporting_data: dict | list
    follow_up_suggestions: list[str] = []


NOT_FOUND_SUGGESTIONS = ["Double-check the tracking number", "Ask about a different shipment"]


def _where_is_my_package(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    timeline = row.get("journey_timeline") or []
    last_hop = timeline[-1] if timeline else {}
    location = last_hop.get("location") or "an unspecified location"
    answer = (
        f"Your package ({row['tracking_id']}) is currently {row['current_status']}, "
        f"last seen at {location}. "
        f"Estimated delivery: {row.get('estimated_delivery') or 'not yet available'}."
    )
    return ShipmentAnswer(
        answer=answer,
        tracking_id=row["tracking_id"],
        current_status=row["current_status"],
        confidence_score=1.0,
        supporting_data=row,
    )


def _why_is_it_late(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    if row.get("reason_for_delay", "NONE") == "NONE":
        answer = (
            f"Your package ({row['tracking_id']}) doesn't currently show a delay — "
            f"status is {row['current_status']}."
        )
    else:
        answer = (
            f"Your package ({row['tracking_id']}) is delayed due to "
            f"{row['reason_for_delay']}. {row.get('delay_comments') or ''} "
            f"Open issues on this shipment: {row.get('open_issue_count', 0)}. "
            f"Estimated delivery: {row.get('estimated_delivery') or 'not yet available'}."
        ).strip()
    return ShipmentAnswer(
        answer=answer,
        tracking_id=row["tracking_id"],
        current_status=row["current_status"],
        confidence_score=1.0,
        supporting_data=row,
    )


def _shipment_customer_lookup(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    profile = row.get("customer_profile") or {}
    contact = profile.get("contact_name")
    answer = f"Package {row['tracking_id']} belongs to {row['org_name']} (account {row['fedex_account_id']})."
    if contact:
        answer += f" Account contact: {contact}."
    return ShipmentAnswer(
        answer=answer,
        tracking_id=row["tracking_id"],
        confidence_score=1.0,
        supporting_data=row,
    )


def _format_loc(loc: dict | None) -> str:
    if not loc:
        return "an unknown location"
    parts = [loc.get("city"), loc.get("state"), loc.get("country_code")]
    return ", ".join(p for p in parts if p) or "an unknown location"


def _shipment_package_details(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    desc = row.get("package_desc") or "no description on file"
    weight = f"{row['package_weight_kg']} kg" if row.get("package_weight_kg") is not None else "unknown weight"
    answer = (
        f"Package {row['tracking_id']} is a {row['package_size']} {row['package_type']} "
        f"({desc}), {weight}, shipped via {row['delivery_type']} service."
    )
    if row.get("order_id"):
        answer += f" Order reference: {row['order_id']}."
    return ShipmentAnswer(answer=answer, tracking_id=row["tracking_id"], confidence_score=1.0, supporting_data=row)


def _shipment_route(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    origin = _format_loc(row.get("src_loc"))
    dest = _format_loc(row.get("dest_loc"))
    scope = "international" if row.get("is_international") else "domestic"
    answer = f"Package {row['tracking_id']} is a {scope} shipment from {origin} to {dest}."
    return ShipmentAnswer(answer=answer, tracking_id=row["tracking_id"], confidence_score=1.0, supporting_data=row)


def _shipment_schedule(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    if row.get("pickup_date"):
        pickup = f"scheduled for pickup on {row['pickup_date']}"
        if row.get("pickup_window_start") and row.get("pickup_window_end"):
            pickup += f" between {row['pickup_window_start']} and {row['pickup_window_end']}"
    else:
        pickup = "has no pickup scheduled"
    answer = f"Package {row['tracking_id']} {pickup}."
    if row.get("delivery_window_start") and row.get("delivery_window_end"):
        answer += f" Delivery window: {row['delivery_window_start']} to {row['delivery_window_end']}."
    answer += f" Estimated delivery: {row.get('estimated_delivery') or 'not yet available'}."
    return ShipmentAnswer(answer=answer, tracking_id=row["tracking_id"], confidence_score=1.0, supporting_data=row)


def _shipment_delivery_attempts(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    attempts = row.get("failed_delivery_attempts") or 0
    if attempts == 0:
        answer = f"Package {row['tracking_id']} has no failed delivery attempts (status: {row['current_status']})."
    else:
        answer = (
            f"Package {row['tracking_id']} has {attempts} failed delivery attempt(s), "
            f"last attempted {row.get('last_delivery_attempt_at') or 'at an unknown time'} "
            f"(status: {row['current_status']})."
        )
    return ShipmentAnswer(
        answer=answer,
        tracking_id=row["tracking_id"],
        current_status=row.get("current_status"),
        confidence_score=1.0,
        supporting_data=row,
    )


def _customs_status(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(
            answer="I couldn't find a shipment with that tracking number.",
            confidence_score=0.3,
            supporting_data=[],
            follow_up_suggestions=NOT_FOUND_SUGGESTIONS,
        )
    row = rows[0]
    if not row["is_international"]:
        answer = f"Package {row['tracking_id']} is a domestic shipment — no customs processing applies."
    else:
        answer = f"Package {row['tracking_id']} customs status: {row['customs_status']}."
    return ShipmentAnswer(
        answer=answer,
        tracking_id=row["tracking_id"],
        confidence_score=1.0,
        supporting_data=row,
    )


def _open_issues_for_shipment(rows: list, tracking_id: str | None) -> ShipmentAnswer:
    if not rows:
        answer = "No open issues found for this shipment."
        confidence = 0.9
    else:
        lines = [f"- {r['issue_type']}: {r.get('description') or 'no description'} ({r['status']})" for r in rows]
        answer = f"Found {len(rows)} open issue(s):\n" + "\n".join(lines)
        confidence = 1.0
    return ShipmentAnswer(
        answer=answer,
        tracking_id=tracking_id,
        confidence_score=confidence,
        supporting_data=rows,
    )


def _ops_daily_briefing(rows: list) -> ShipmentAnswer:
    if not rows:
        answer = "No open issues across the fleet right now."
    else:
        lines = [f"- {r['issue_type']}: {r['issue_count']} open (avg {r['avg_age_or_resolution_hours']}h)" for r in rows]
        answer = "Today's critical shipment issues:\n" + "\n".join(lines)
    return ShipmentAnswer(answer=answer, confidence_score=1.0, supporting_data=rows)


def _top_customers_by_volume(rows: list) -> ShipmentAnswer:
    if not rows:
        answer = "No customer volume data available yet."
    else:
        lines = [f"- {r['org_name']}: {r['shipment_count']} shipments, {r['on_time_pct']}% on-time" for r in rows[:10]]
        answer = "Top shippers by volume:\n" + "\n".join(lines)
    return ShipmentAnswer(answer=answer, confidence_score=1.0, supporting_data=rows)


def _format_list(rows: list, header: str, empty_message: str, line_fn) -> ShipmentAnswer:
    """Shared shape for the fleet-wide breakdown views below — one bullet
    per row via `line_fn`, a count-bearing header, and an honest low-
    confidence empty case rather than a bare empty sentence."""
    if not rows:
        return ShipmentAnswer(answer=empty_message, confidence_score=0.9, supporting_data=[])
    lines = [line_fn(r) for r in rows]
    return ShipmentAnswer(answer=f"{header} ({len(rows)}):\n" + "\n".join(lines), confidence_score=1.0, supporting_data=rows)


# --- Zero-param dashboard views (8 new — one per previously-unwired view) ---

def _dashboard_headline(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(answer="No dashboard data available.", confidence_score=0.0, supporting_data=[])
    r = rows[0]
    answer = (
        f"{r['total_shipments']} total shipments across {r['total_customers']} customers. "
        f"{r['delivered_count']} delivered ({r['on_time_pct']}% on-time). "
        f"{r['in_transit_overdue']} in-transit shipments are overdue. {r['open_issues']} open issues. "
        f"{r['international_shipments']} international shipments, {r['customs_held_now']} currently held in customs. "
        f"{r['lost_count']} lost, {r['returned_count']} returned, {r['cancelled_count']} cancelled."
    )
    return ShipmentAnswer(answer=answer, confidence_score=1.0, supporting_data=r)


def _status_breakdown(rows: list) -> ShipmentAnswer:
    return _format_list(
        rows, "Shipment status breakdown", "No status data available.",
        lambda r: f"- {r['current_status']}: {r['shipment_count']} ({r['pct_of_total']}%)",
    )


def _ontime_performance(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(answer="No on-time performance data available.", confidence_score=0.0, supporting_data=[])
    r = rows[0]
    late_note = (
        f", averaging {r['avg_delay_hours_when_late']}h late when delayed"
        if r.get("avg_delay_hours_when_late") is not None else ""
    )
    answer = (
        f"{r['delivered_count']} shipments delivered so far — {r['on_time_pct']}% on-time "
        f"({r['delivered_on_time']} on-time, {r['delivered_late']} late{late_note}). "
        f"{r['in_transit_overdue']} shipments currently in transit are already overdue."
    )
    return ShipmentAnswer(answer=answer, confidence_score=1.0, supporting_data=r)


def _delay_reason_breakdown(rows: list) -> ShipmentAnswer:
    return _format_list(
        rows, "Delay reason breakdown", "No delayed shipments right now.",
        lambda r: f"- {r['reason_for_delay']}: {r['shipment_count']} ({r['pct_of_delayed']}% of delayed)",
    )


def _domestic_vs_international_split(rows: list) -> ShipmentAnswer:
    return _format_list(
        rows, "Domestic vs international split", "No shipment scope data available.",
        lambda r: (f"- {r['shipment_scope']}: {r['shipment_count']} shipments "
                   f"(customs held: {r['customs_held']}, pending: {r['customs_pending']}, cleared: {r['customs_cleared']})"),
    )


def _daily_volume_trend(rows: list) -> ShipmentAnswer:
    return _format_list(
        rows, f"Daily volume trend (last {len(rows)} days)", "No volume trend data available.",
        lambda r: f"- {r['day']}: {r['shipments_created']} created, {r['shipments_delivered']} delivered",
    )


def _service_level_mix(rows: list) -> ShipmentAnswer:
    return _format_list(
        rows, "Delivery service level performance", "No service level data available.",
        lambda r: f"- {r['delivery_type']}: {r['shipment_count']} shipments, {r['on_time_pct']}% on-time",
    )


def _chat_activity_summary(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(answer="No chat activity data available.", confidence_score=0.0, supporting_data=[])
    r = rows[0]
    answer = (
        f"{r['total_chat_interactions']} total chat interactions across {r['shipments_with_chat']} shipments. "
        f"Average confidence: {r['avg_confidence']}. "
        f"{r['low_confidence_needing_review']} interaction(s) flagged for human review (confidence < 0.75)."
    )
    return ShipmentAnswer(answer=answer, confidence_score=1.0, supporting_data=r)


# --- Mix-and-match filtered/joined templates (6 new) ------------------------

def _shipments_by_customer(rows: list, org_name: str | None) -> ShipmentAnswer:
    who = org_name or "that customer"
    if not rows:
        return ShipmentAnswer(answer=f"No shipments found for {who}.", confidence_score=0.5, supporting_data=[])
    lines = [
        f"- {r['tracking_id']}: {r['current_status']}"
        + (f" (delayed: {r['reason_for_delay']})" if r.get("reason_for_delay") not in (None, "NONE") else "")
        for r in rows
    ]
    return ShipmentAnswer(answer=f"{who} has {len(rows)} shipment(s):\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_customer_delayed(rows: list, org_name: str | None) -> ShipmentAnswer:
    who = org_name or "that customer"
    if not rows:
        return ShipmentAnswer(answer=f"None of {who}'s shipments are currently delayed.",
                               confidence_score=0.9, supporting_data=[])
    lines = [
        f"- {r['tracking_id']}: {r['current_status']}, delayed due to {r['reason_for_delay']}"
        + (f" — {r['delay_comments']}" if r.get("delay_comments") else "")
        for r in rows
    ]
    return ShipmentAnswer(answer=f"{len(rows)} of {who}'s shipments are currently delayed:\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_status(rows: list, status: str | None) -> ShipmentAnswer:
    label = status or "that status"
    if not rows:
        return ShipmentAnswer(answer=f"No shipments currently have status {label}.",
                               confidence_score=0.9, supporting_data=[])
    lines = [
        f"- {r['tracking_id']}"
        + (f" (delayed: {r['reason_for_delay']})" if r.get("reason_for_delay") not in (None, "NONE") else "")
        for r in rows
    ]
    return ShipmentAnswer(answer=f"{len(rows)} shipment(s) with status {label}:\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_package_type(rows: list, package_type: str | None) -> ShipmentAnswer:
    label = package_type or "that package type"
    if not rows:
        return ShipmentAnswer(answer=f"No {label} shipments found.", confidence_score=0.9, supporting_data=[])
    lines = [f"- {r['tracking_id']}: {r['current_status']} ({r.get('package_size', 'unknown size')})" for r in rows]
    return ShipmentAnswer(answer=f"{len(rows)} {label} shipment(s):\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_delivery_type(rows: list, delivery_type: str | None) -> ShipmentAnswer:
    label = delivery_type or "that delivery type"
    if not rows:
        return ShipmentAnswer(answer=f"No {label} shipments found.", confidence_score=0.9, supporting_data=[])
    lines = [f"- {r['tracking_id']}: {r['current_status']}, ETA {r.get('estimated_delivery') or 'unknown'}" for r in rows]
    return ShipmentAnswer(answer=f"{len(rows)} {label} shipment(s):\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _failed_delivery_shipments(rows: list) -> ShipmentAnswer:
    if not rows:
        return ShipmentAnswer(answer="No shipments currently have failed delivery attempts.",
                               confidence_score=0.9, supporting_data=[])
    lines = [
        f"- {r['tracking_id']}: {r['failed_delivery_attempts']} failed attempt(s), "
        f"last at {r.get('last_delivery_attempt_at') or 'unknown'}"
        for r in rows
    ]
    return ShipmentAnswer(answer=f"{len(rows)} shipment(s) with failed delivery attempts:\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_location(rows: list, location: str | None) -> ShipmentAnswer:
    where = location or "that location"
    if not rows:
        return ShipmentAnswer(answer=f"No shipments found going to or from {where}.",
                               confidence_score=0.5, supporting_data=[])
    lines = []
    for r in rows:
        src = (r.get("src_loc") or {}).get("city", "?")
        dest = (r.get("dest_loc") or {}).get("city", "?")
        lines.append(f"- {r['tracking_id']}: {r['current_status']} ({src} -> {dest})")
    return ShipmentAnswer(answer=f"{len(rows)} shipment(s) touching {where}:\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_package_size(rows: list, package_size: str | None) -> ShipmentAnswer:
    label = package_size or "that size"
    if not rows:
        return ShipmentAnswer(answer=f"No {label} shipments found.", confidence_score=0.9, supporting_data=[])
    lines = [f"- {r['tracking_id']}: {r['current_status']} ({r.get('package_type', 'unknown type')})" for r in rows]
    return ShipmentAnswer(answer=f"{len(rows)} {label} shipment(s):\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


def _shipments_by_pickup_date(rows: list, pickup_date: str | None) -> ShipmentAnswer:
    when = pickup_date or "that date"
    if not rows:
        return ShipmentAnswer(answer=f"No shipments are scheduled for pickup on {when}.",
                               confidence_score=0.9, supporting_data=[])
    lines = [
        f"- {r['tracking_id']}: {r['current_status']}"
        + (f" ({r['pickup_window_start']}-{r['pickup_window_end']})" if r.get("pickup_window_start") else "")
        for r in rows
    ]
    return ShipmentAnswer(answer=f"{len(rows)} shipment(s) scheduled for pickup on {when}:\n" + "\n".join(lines),
                           confidence_score=1.0, supporting_data=rows)


_FORMATTERS = {
    "where_is_my_package": lambda rows, entities: _where_is_my_package(rows),
    "why_is_it_late": lambda rows, entities: _why_is_it_late(rows),
    "shipment_customer_lookup": lambda rows, entities: _shipment_customer_lookup(rows),
    "shipment_package_details": lambda rows, entities: _shipment_package_details(rows),
    "shipment_route": lambda rows, entities: _shipment_route(rows),
    "shipment_schedule": lambda rows, entities: _shipment_schedule(rows),
    "shipment_delivery_attempts": lambda rows, entities: _shipment_delivery_attempts(rows),
    "customs_status": lambda rows, entities: _customs_status(rows),
    "open_issues_for_shipment": lambda rows, entities: _open_issues_for_shipment(rows, entities.tracking_id),
    "ops_daily_briefing": lambda rows, entities: _ops_daily_briefing(rows),
    "top_customers_by_volume": lambda rows, entities: _top_customers_by_volume(rows),
    "dashboard_headline": lambda rows, entities: _dashboard_headline(rows),
    "status_breakdown": lambda rows, entities: _status_breakdown(rows),
    "ontime_performance": lambda rows, entities: _ontime_performance(rows),
    "delay_reason_breakdown": lambda rows, entities: _delay_reason_breakdown(rows),
    "domestic_vs_international_split": lambda rows, entities: _domestic_vs_international_split(rows),
    "daily_volume_trend": lambda rows, entities: _daily_volume_trend(rows),
    "service_level_mix": lambda rows, entities: _service_level_mix(rows),
    "chat_activity_summary": lambda rows, entities: _chat_activity_summary(rows),
    "shipments_by_customer": lambda rows, entities: _shipments_by_customer(rows, entities.org_name),
    "shipments_by_customer_delayed": lambda rows, entities: _shipments_by_customer_delayed(rows, entities.org_name),
    "shipments_by_status": lambda rows, entities: _shipments_by_status(rows, entities.enum_matches.get("current_status")),
    "shipments_by_package_type": lambda rows, entities: _shipments_by_package_type(rows, entities.enum_matches.get("package_type")),
    "shipments_by_delivery_type": lambda rows, entities: _shipments_by_delivery_type(rows, entities.enum_matches.get("delivery_type")),
    "failed_delivery_shipments": lambda rows, entities: _failed_delivery_shipments(rows),
    "shipments_by_location": lambda rows, entities: _shipments_by_location(rows, entities.location),
    "shipments_by_package_size": lambda rows, entities: _shipments_by_package_size(rows, entities.enum_matches.get("package_size")),
    "shipments_by_pickup_date": lambda rows, entities: _shipments_by_pickup_date(rows, entities.dates[0][:10] if entities.dates else None),
}


def format_response(intent: str, rows: list, entities) -> ShipmentAnswer:
    formatter = _FORMATTERS.get(intent)
    if formatter is None:
        return ShipmentAnswer(
            answer="I wasn't able to generate a response for this query.",
            confidence_score=0.0,
            supporting_data=rows,
        )
    return formatter(rows, entities)
