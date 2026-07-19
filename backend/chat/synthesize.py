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


def synthesize(query: str, rows: list) -> ShipmentAnswer:
    system_prompt = (
        "You answer questions about shipments for a logistics chat agent, using ONLY the "
        "row data below — never state a fact not present in it. If the data only partially "
        "answers the question, say so explicitly and lower confidence_score accordingly. If "
        "there are zero rows, say clearly that nothing matching the question was found.\n\n"
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
    if result is None:
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
