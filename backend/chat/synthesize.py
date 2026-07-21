"""
Stage 7 v1 — LLM Response Synthesis (v1 addition, Stage-4b path only).

Does not replace Stage 7 v0 — it's the formatter for the one case Stage 7 v0
structurally can't handle: a query with no matching known intent, whose SQL
Stage 4b drafted instead of a template. Stage 4a's output always goes to
Stage 7 v0 (unchanged, 0 LLM cost); only Stage 4b's output reaches this
module. See AGENTIC_RAG_ARCHITECTURE.md §4 Stage 7 v1 for the routing
rationale and the "why not a full replacement" reasoning.
"""
import json
from datetime import datetime, timezone

from . import llm_client
from .respond_template import ShipmentAnswer

MAX_ROWS_IN_PROMPT = 50

TOOL_NAME = "shipment_answer"
TOOL_SCHEMA = {
    "description": "Produce the customer-facing answer to the user's question, grounded ONLY in the row data provided.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Natural-language answer, grounded only in the row data — "
                               "never state a fact that isn't present in it.",
            },
            "tracking_id": {
                "type": ["string", "null"],
                "description": "The tracking_id this answer is about, if scoped to one shipment; null otherwise.",
            },
            "current_status": {
                "type": ["string", "null"],
                "description": "The shipment's current_status, if present in the data; null otherwise.",
            },
            "confidence_score": {
                "type": "number",
                "description": "0-1: how completely the row data answers the question. "
                                "Lower this if the data only partially addresses it.",
            },
            "follow_up_suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "0-3 short follow-up questions the user might reasonably ask next.",
            },
        },
        "required": ["answer", "confidence_score"],
    },
}


def _rows_for_prompt(rows: list) -> str:
    shown = rows[:MAX_ROWS_IN_PROMPT]
    text = json.dumps(shown, default=str)
    if len(rows) > MAX_ROWS_IN_PROMPT:
        text += f"\n... ({len(rows) - MAX_ROWS_IN_PROMPT} more rows not shown)"
    return text


def _filter_context(sql: str | None, params: dict | None) -> str:
    """Live query: "show tracking ids for customer Daniel and Sons" correctly
    drafted `... JOIN customers c ... WHERE c.org_name = %(org_name)s` and
    correctly returned 23 matching tracking_ids — but the SELECT list only
    had `tracking_id`, no `org_name` column, and synthesize() previously had
    no idea a WHERE clause had run at all. Result: the LLM saw rows with no
    customer-name field and concluded "I don't have customer name
    information," flatly contradicting the fact that filtering by customer
    name is exactly what had just happened server-side. A column's absence
    from the OUTPUT is not evidence the underlying relationship doesn't
    exist — it's still one bare row-list away from that filter being visible.
    Passing the already-executed SQL + resolved params closes that gap."""
    if not sql:
        return ""
    params_text = ", ".join(f"{k} = {v!r}" for k, v in (params or {}).items()) or "(none)"
    return (
        f"\nSQL ALREADY EXECUTED (for your context only — do not repeat it to the user):\n{sql}\n"
        f"PARAMETER VALUES USED: {params_text}\n"
        "This query's WHERE clause has ALREADY filtered ROW DATA according to the user's "
        "specific question — every row below already satisfies it, even when the filtered-on "
        "column (e.g. a customer name used only in a JOIN condition) isn't repeated in the "
        "SELECT output. Never claim a fact 'isn't available' or 'doesn't exist in the data' "
        "just because a column is absent from ROW DATA — check whether it was already used to "
        "produce exactly these rows before concluding that.\n"
    )


def synthesize(query: str, rows: list, sql: str | None = None, params: dict | None = None) -> ShipmentAnswer:
    system_prompt = (
        "You answer questions about shipments for a logistics chat agent, using ONLY the "
        "row data below — never state a fact not present in it. If the data only partially "
        "answers the question, say so explicitly and lower confidence_score accordingly. If "
        "there are zero rows, say clearly that nothing matching the question was found.\n\n"
        f"CURRENT DATE/TIME: {datetime.now(timezone.utc).isoformat()} — use this as \"now\" for "
        "any relative-time reasoning (\"how long ago\", \"more than N days\", \"this week\"). "
        "Do the arithmetic yourself from the timestamps in ROW DATA; never say you lack a "
        "reference date.\n"
        f"{_filter_context(sql, params)}\n"
        f"ROW DATA:\n{_rows_for_prompt(rows)}\n\n"
        f"You MUST respond by calling the {TOOL_NAME} function — never respond with plain "
        "text, explanation, or reasoning outside of it. Call the function now."
    )

    result = llm_client.call_tool(
        system_prompt=system_prompt,
        user_message=query,
        tool_name=TOOL_NAME,
        tool_schema=TOOL_SCHEMA,
    )
    # Live case: the tool call itself succeeded (result is a real dict, not None)
    # but the model left "answer" blank — reproduced directly against the same
    # prompt/rows and got a full, correct answer on the very next attempt, so
    # this is LLM output non-determinism, not a deterministic prompt bug. Empty
    # "answer" must degrade the same way `result is None` already does — a
    # blank string is a broken response, not a legitimate empty answer, even
    # though the call "succeeded" by the only check that used to run.
    if result is None or not result.get("answer"):
        return ShipmentAnswer(
            answer="I found some data but wasn't able to generate a clear answer from it.",
            confidence_score=0.0,
            supporting_data=rows,
        )

    return ShipmentAnswer(
        answer=result.get("answer", ""),
        tracking_id=result.get("tracking_id"),
        current_status=result.get("current_status"),
        confidence_score=float(result.get("confidence_score", 0.5)),
        supporting_data=rows,
        follow_up_suggestions=result.get("follow_up_suggestions") or [],
    )
