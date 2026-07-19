"""
Orchestrates Stages 1-7 as a plain sequential generator — not a
LangChain/LangGraph agent (see AGENTIC_RAG_ARCHITECTURE.md §6: "the control
flow is fixed, so a graph/agent framework would add indirection without
adding capability at this scale"). v0 only: Stage 4b and the LLM half of
Stage 7 don't exist yet, so unmatched/low-confidence queries get a static
clarifying response instead of an LLM fallback.

Yields one {"stage": ..., "detail": ...} trace event per pipeline step,
ending with a final "answer_ready" event. router.py decides whether to
stream every event (verbose/privileged callers) or just the last one.
"""
import concurrent.futures

from . import intent as intent_stage
from . import entities as entity_stage
from . import schema_scope
from . import sql_templates
from . import guardrails
from . import executor
from . import respond_template
from .audit import log_chat_interaction

CLARIFYING_ANSWER = {
    "answer": (
        "I can currently help with: where a package is, why it's delayed, "
        "customs status, open issues on a shipment, today's critical issues, "
        "and top shippers by volume. Could you rephrase, or include a "
        "tracking number?"
    ),
    "confidence_score": 0.0,
    "supporting_data": {},
}

REJECTED_ANSWER = {
    "answer": "I generated a query that didn't pass safety validation, so I'm not going to run it.",
    "confidence_score": 0.0,
    "supporting_data": {},
}


def _multi_id_answer(tracking_ids: list) -> dict:
    ids_str = " and ".join(tracking_ids) if len(tracking_ids) == 2 else ", ".join(tracking_ids)
    return {
        "answer": (
            f"I can look up one shipment at a time right now — you mentioned {ids_str}. "
            "Which one would you like details on? (Comparing multiple shipments in a "
            "single answer isn't supported yet.)"
        ),
        "confidence_score": 0.0,
        "supporting_data": {"tracking_ids_found": tracking_ids},
    }


def _classify_and_extract(query: str):
    # Stage 1 and Stage 2 only need the raw query text — no dependency on
    # each other — so they run concurrently (AGENTIC_RAG_ARCHITECTURE.md §3).
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        intent_future = pool.submit(intent_stage.classify_intent, query)
        entities_future = pool.submit(entity_stage.extract_entities, query)
        return intent_future.result(), entities_future.result()


def _finish(query: str, extracted, intent_name, answer_payload: dict, context_rows):
    # Audit write happens BEFORE the final yield, not after, so it still
    # runs even if the caller stops iterating right after "answer_ready".
    log_chat_interaction(
        tracking_id=extracted.tracking_id,
        customer_id=None,  # no auth/session model in Phase 1
        user_query=query,
        ai_response=answer_payload.get("answer", ""),
        context_snapshot={"intent": intent_name, "rows": context_rows},
        confidence_score=answer_payload.get("confidence_score"),
    )
    yield {"stage": "answer_ready", "detail": answer_payload}


def run_pipeline(query: str):
    intent_result, extracted = _classify_and_extract(query)
    yield {"stage": "intent_classified", "detail": {
        "intent": intent_result.intent,
        "confidence": round(intent_result.confidence, 3),
    }}
    yield {"stage": "entities_extracted", "detail": {
        "tracking_id": extracted.tracking_id,
        "tracking_ids": extracted.tracking_ids,
        "enum_matches": extracted.enum_matches,
        "org_name": extracted.org_name,
        "dates": extracted.dates,
    }}

    if len(extracted.tracking_ids) > 1:
        # No v0 template can compare/combine multiple shipments in one answer —
        # answering about only the first (which fill_template would silently do)
        # is a worse failure than declining, so this is checked before routing.
        yield {"stage": "multiple_tracking_ids_detected", "detail": {
            "reason": "no v0 template supports comparing multiple shipments; "
                      "answering about only the first would be misleading",
            "tracking_ids": extracted.tracking_ids,
        }}
        yield from _finish(query, extracted, None, _multi_id_answer(extracted.tracking_ids), [])
        return

    scoped = schema_scope.scope_schema(query)
    yield {"stage": "schema_scoped", "detail": {
        "entities": scoped.entities,
        "scores": {k: round(v, 3) for k, v in scoped.scores.items()},
    }}

    resolved_intent = intent_result.intent

    # Guard: a tracking_id in the query is strong evidence this is about ONE
    # shipment. If the classifier nonetheless matched a fleet-wide intent
    # (one whose template needs no tracking_id — ops_daily_briefing,
    # top_customers_by_volume), that's more likely a lower-threshold
    # misclassification than a genuine fleet-wide question that happens to
    # contain a 9-15 digit number. A "not found" answer from the wrong
    # template is a far more honest failure than a confidently irrelevant
    # fleet-wide report — see AGENTIC_RAG_ARCHITECTURE.md's corner-case audit.
    template_spec = sql_templates.TEMPLATES.get(resolved_intent) if resolved_intent else None
    if (template_spec is not None and extracted.tracking_id
            and "tracking_id" not in template_spec.required):
        yield {"stage": "intent_overridden", "detail": {
            "reason": "classifier matched a fleet-wide intent despite a tracking_id "
                      "being present in the query — overriding to a shipment-scoped lookup",
            "original_intent": resolved_intent,
            "overridden_to": "where_is_my_package",
        }}
        resolved_intent = "where_is_my_package"

    if resolved_intent is None and extracted.tracking_id:
        # Below-threshold confidence, but exactly one tracking_id was found —
        # default to the general status lookup rather than declining outright.
        # This is a deliberate fallback, not a real classification, so it's
        # flagged in the trace for verbose/privileged viewers to distinguish
        # from an actual confident match.
        resolved_intent = "where_is_my_package"
        yield {"stage": "intent_defaulted", "detail": {
            "reason": "confidence below threshold but a tracking_id was found — "
                      "defaulting to a general status lookup instead of declining",
            "intent": resolved_intent,
        }}

    if resolved_intent is None:
        yield {"stage": "no_intent_match", "detail": {
            "reason": "confidence below threshold, no tracking_id to fall back on, "
                      "no LLM fallback in v0",
        }}
        yield from _finish(query, extracted, None, CLARIFYING_ANSWER, [])
        return

    filled = sql_templates.fill_template(resolved_intent, extracted)
    if filled is None:
        yield {"stage": "sql_generation_failed", "detail": {
            "reason": "intent matched but a required entity (e.g. tracking_id) "
                      "wasn't found in the query, and there's no LLM fallback in v0",
        }}
        yield from _finish(query, extracted, resolved_intent, CLARIFYING_ANSWER, [])
        return

    yield {"stage": "sql_generated", "detail": {
        "sql": filled.sql.strip(), "params": filled.params, "source": "template",
    }}

    try:
        validated_sql = guardrails.validate_sql(filled.sql, [filled.table_key])
    except guardrails.GuardrailError as exc:
        yield {"stage": "sql_rejected", "detail": {"reason": str(exc)}}
        yield from _finish(query, extracted, resolved_intent, REJECTED_ANSWER, [])
        return

    yield {"stage": "sql_validated", "detail": {"status": "accepted"}}
    yield {"stage": "executing", "detail": {}}

    result = executor.execute_query(validated_sql, filled.params)
    yield {"stage": "rows_returned", "detail": {
        "count": result.row_count, "elapsed_ms": result.elapsed_ms,
    }}

    answer = respond_template.format_response(resolved_intent, result.rows, extracted)
    yield from _finish(query, extracted, resolved_intent, answer.model_dump(), result.rows)
