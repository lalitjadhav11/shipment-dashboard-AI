"""
Orchestrates Stages 1-7 as a plain sequential generator — not a
LangChain/LangGraph agent (see AGENTIC_RAG_ARCHITECTURE.md §6: "the control
flow is fixed, so a graph/agent framework would add indirection without
adding capability at this scale").

v1: Stage 4a (templates) is always tried first, for every query — Stage 4b
(LLM SQL draft) only runs when Stage 4a produces nothing, whether because no
intent matched or a matched intent's required entity was missing. Stage 7 is
then routed by which stage produced the SQL: Stage 4a's output always goes
to the zero-cost v0 template formatter; only Stage 4b's output reaches the
v1 LLM synthesizer. Without an ANTHROPIC_API_KEY configured, sql_llm.draft_sql
returns None immediately and behavior degrades to v0's clarifying answer —
see AGENTIC_RAG_ARCHITECTURE.md §2/§4 for the full routing rationale.

Yields one {"stage": ..., "detail": ...} trace event per pipeline step,
ending with a final "answer_ready" event. router.py decides whether to
stream every event (verbose/privileged callers) or just the last one.
"""
import concurrent.futures

from . import intent as intent_stage
from . import entities as entity_stage
from . import schema_scope
from . import sql_templates
from . import sql_llm
from . import guardrails
from . import executor
from . import respond_template
from . import synthesize
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

EXECUTION_FAILED_ANSWER = {
    "answer": "That query took too long to run (or hit a database error), so I wasn't able to get an answer. Try narrowing your question.",
    "confidence_score": 0.0,
    "supporting_data": {},
}

# Only tracking_id has a blind-default/override shortcut below — org_name and
# location never had an equivalent, so an unmatched query mentioning either
# already falls straight through to Stage 4b today; nothing to fix there.
MINIMAL_QUERY_MAX_WORDS = 4

# Live query: "why there are so many orders held at customs" confidently matched
# shipments_by_status (a fillable, non-explanatory template — just a list of
# tracking IDs) instead of falling through to Stage 4b, so the answer never
# addressed "why" at all — no formatting fix could have helped, because the
# template's SQL never captured a cause to format. See
# AGENTIC_RAG_ARCHITECTURE.md §15. Deliberately narrow phrasing (not "how"/
# "what" — those are legitimately answered by lookups/breakdowns). Detector
# lives in schema_scope.py (schema_scope.is_causal_query) since Stage 3 also
# needs it, to force shipment_issue into scope for the same queries — one
# definition, reused here rather than duplicated (see §15.1).


def _is_minimal_query(query: str, tracking_ids: list) -> bool:
    """True for a genuinely bare query — just a tracking number, or barely
    more ("wheres X") — where defaulting to a general status lookup is a
    safe, useful guess. False for a longer, clearly-articulated question
    that happens to mention a tracking_id but is asking something more
    specific ("what was the previous stage of X") — forcing THAT into a
    fixed-shape template answers the wrong question confidently instead of
    honestly, which is worse than trying Stage 4b or declining. Tuned so the
    already-verified corner-case-audit regressions ("tell me about shipment
    X" -> 4 words, "give me details about X" -> 4 words) still take the
    default, while "what was the previous stage of X" (6 words) doesn't."""
    stripped = query
    for tid in tracking_ids:
        stripped = stripped.replace(tid, " ")
    return len(stripped.split()) <= MINIMAL_QUERY_MAX_WORDS


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

    scoped = schema_scope.scope_schema(query, extracted)
    yield {"stage": "schema_scoped", "detail": {
        "entities": scoped.entities,
        "scores": {k: round(v, 3) for k, v in scoped.scores.items()},
        "forced_entities": scoped.forced_entities,
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
    #
    # But only default to where_is_my_package for a genuinely minimal query.
    # A longer, specific question ("what's the average delay excluding
    # 800000000131") shouldn't be force-answered as a single-shipment status
    # lookup either — clear the match entirely and let Stage 4b (which
    # already gets the right raw entity scoped in via Stage 3's identity
    # forcing) decide what the question actually needs.
    template_spec = sql_templates.TEMPLATES.get(resolved_intent) if resolved_intent else None
    if (template_spec is not None and extracted.tracking_id
            and "tracking_id" not in template_spec.required):
        if _is_minimal_query(query, extracted.tracking_ids):
            yield {"stage": "intent_overridden", "detail": {
                "reason": "classifier matched a fleet-wide intent despite a tracking_id "
                          "being present in the query — overriding to a shipment-scoped lookup",
                "original_intent": resolved_intent,
                "overridden_to": "where_is_my_package",
            }}
            resolved_intent = "where_is_my_package"
        else:
            yield {"stage": "intent_overridden", "detail": {
                "reason": "classifier matched a fleet-wide intent despite a tracking_id "
                          "being present, and the query is too specific to safely default to "
                          "a shipment-scoped lookup either — clearing the match for Stage 4b",
                "original_intent": resolved_intent,
                "overridden_to": None,
            }}
            resolved_intent = None

    if resolved_intent is None and extracted.tracking_id:
        if _is_minimal_query(query, extracted.tracking_ids):
            # Genuinely bare query ("800000000131", "wheres X") — default to
            # the general status lookup rather than declining outright. This
            # is a deliberate fallback, not a real classification, so it's
            # flagged in the trace for verbose/privileged viewers to
            # distinguish it from an actual confident match.
            resolved_intent = "where_is_my_package"
            yield {"stage": "intent_defaulted", "detail": {
                "reason": "confidence below threshold but a tracking_id was found, and "
                          "the query is minimal enough that a general status lookup is a "
                          "safe guess",
                "intent": resolved_intent,
            }}
        else:
            # A real, specific question that just didn't match any known
            # template — forcing it into where_is_my_package would answer
            # confidently but wrong (e.g. "what was the previous stage of
            # X" got told the *current* status). Leave resolved_intent as
            # None so it falls through to the Stage 4b attempt below
            # instead of a mismatched canned answer.
            yield {"stage": "default_skipped_too_specific", "detail": {
                "reason": "tracking_id found but the query is too specific for a blind "
                          "'where is it' default — trying Stage 4b so the actual question "
                          "gets a chance at a real answer",
            }}

    filled = None
    if resolved_intent is not None:
        filled = sql_templates.fill_template(resolved_intent, extracted)
        if filled is None:
            yield {"stage": "sql_generation_failed", "detail": {
                "reason": "intent matched but a required entity (e.g. tracking_id) "
                          "wasn't found in the query — trying the Stage 4b LLM fallback",
            }}
        elif schema_scope.is_causal_query(query) and not sql_templates.TEMPLATES[resolved_intent].explains_causation:
            # A successfully-filled template is not necessarily the RIGHT answer to a
            # "why" question — a lookup or breakdown-by-reason template runs fine and
            # returns rows, but never captures a cause, so its answer would silently
            # not address the question. Treat this the same as an unfilled template:
            # discard it and let Stage 4b (which can draft a query AND explain the
            # result in prose) have a real shot instead.
            yield {"stage": "causal_query_needs_llm", "detail": {
                "reason": "the query asks 'why', but the matched template only looks up "
                          "or counts records rather than explaining a cause — routing to "
                          "Stage 4b instead of returning a non-answer",
                "intent": resolved_intent,
            }}
            filled = None
        elif schema_scope.wants_individual_records(query, extracted) and sql_templates.TEMPLATES[resolved_intent].is_aggregate:
            # Live query: "show me all custom shipments those are impacted due to weather
            # delay" — entity extraction correctly found package_type=CUSTOM AND
            # reason_for_delay=WEATHER, and Stage 3 correctly forced `shipment` into scope
            # (this IS a list-of-records question) — but Stage 1 still confidently matched
            # delay_reason_breakdown, a zero-param aggregate that "fills" trivially and
            # never even looks at the two filters the user actually gave. Same shape as
            # the causal gate above, different signal: a successfully-filled AGGREGATE
            # template is not the right answer to a "show me all X" question, regardless
            # of how confidently it matched. See AGENTIC_RAG_ARCHITECTURE.md §18.
            yield {"stage": "list_query_needs_llm", "detail": {
                "reason": "the query asks for individual shipment records, but the matched "
                          "template only returns a fleet-wide aggregate/breakdown — routing "
                          "to Stage 4b instead of returning an irrelevant summary",
                "intent": resolved_intent,
            }}
            filled = None
        elif schema_scope.wants_history(query) and not sql_templates.TEMPLATES[resolved_intent].shows_full_history:
            # Live bug: "give me the status history for 800000000073" matched
            # where_is_my_package (tracking_id present, fills trivially) and returned
            # "currently OUT_FOR_DELIVERY, last seen at Melbourne" — true, but not a history,
            # since that formatter only reads the LAST hop of journey_timeline even though the
            # SQL happens to select the whole thing. Same shape as the causal/aggregate gates
            # above: a successfully-filled template whose ANSWER doesn't actually address what
            # was asked is not an answer. Discard it and let Stage 4b narrate the full timeline
            # instead. See AGENTIC_RAG_ARCHITECTURE.md §22.
            yield {"stage": "history_query_needs_llm", "detail": {
                "reason": "the query asks for status HISTORY/timeline, but the matched "
                          "template's answer only reports the current/last-known stage — "
                          "routing to Stage 4b so the full journey can be narrated",
                "intent": resolved_intent,
            }}
            filled = None

    if filled is None:
        # Stage 4a is exhausted — either nothing matched, or something matched
        # but couldn't be filled. Try the LLM fallback (v1) before declining.
        # Waterfall, not merge: this is the ONLY entry point into Stage 4b,
        # and it's only reached after Stage 4a has already had its shot.
        yield {"stage": "llm_sql_fallback_attempting", "detail": {
            "entities": scoped.entities,
        }}
        filled = sql_llm.draft_sql(query, scoped.entities, extracted)
        if filled is None:
            yield {"stage": "no_intent_match", "detail": {
                "reason": "no template matched and the LLM fallback (if configured) "
                          "couldn't draft a usable query",
            }}
            yield from _finish(query, extracted, resolved_intent, CLARIFYING_ANSWER, [])
            return

    yield {"stage": "sql_generated", "detail": {
        "sql": filled.sql.strip(), "params": filled.params, "source": filled.source,
    }}

    try:
        validated_sql = guardrails.validate_sql(filled.sql, filled.entity_keys)
    except guardrails.GuardrailError as exc:
        yield {"stage": "sql_rejected", "detail": {"reason": str(exc)}}
        yield from _finish(query, extracted, resolved_intent, REJECTED_ANSWER, [])
        return

    yield {"stage": "sql_validated", "detail": {"status": "accepted"}}
    yield {"stage": "executing", "detail": {}}

    try:
        result = executor.execute_query(validated_sql, filled.params)
    except executor.ExecutionError as exc:
        # Structurally-safe SQL can still fail at runtime (timeout, deadlock)
        # — must degrade cleanly, not crash the stream. Same defensive
        # pattern as the audit-log FK-violation fix.
        yield {"stage": "execution_failed", "detail": {"reason": str(exc)}}
        yield from _finish(query, extracted, resolved_intent, EXECUTION_FAILED_ANSWER, [])
        return
    yield {"stage": "rows_returned", "detail": {
        "count": result.row_count, "elapsed_ms": result.elapsed_ms,
    }}

    # Stage 7 routing: Stage 4a's output always goes to the v0 template
    # formatter (0 LLM cost, unchanged, proven); only Stage 4b's output
    # reaches the v1 LLM synthesizer — see AGENTIC_RAG_ARCHITECTURE.md §4
    # Stage 7 for why this is routing, not a full replacement.
    if filled.source == "llm":
        answer = synthesize.synthesize(query, result.rows, sql=validated_sql, params=filled.params)
    else:
        answer = respond_template.format_response(resolved_intent, result.rows, extracted)

    audit_intent = resolved_intent or "llm_drafted"
    yield from _finish(query, extracted, audit_intent, answer.model_dump(), result.rows)
