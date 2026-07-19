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


_FORMATTERS = {
    "where_is_my_package": lambda rows, entities: _where_is_my_package(rows),
    "why_is_it_late": lambda rows, entities: _why_is_it_late(rows),
    "customs_status": lambda rows, entities: _customs_status(rows),
    "open_issues_for_shipment": lambda rows, entities: _open_issues_for_shipment(rows, entities.tracking_id),
    "ops_daily_briefing": lambda rows, entities: _ops_daily_briefing(rows),
    "top_customers_by_volume": lambda rows, entities: _top_customers_by_volume(rows),
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
