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
import time

from . import intent as intent_stage
from . import entities as entity_stage
from . import schema_scope
from . import session as session_stage
from . import sql_templates
from . import sql_llm
from . import guardrails
from . import executor
from . import respond_template
from . import synthesize
from . import llm_client
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


def _fmt_loc_or_date(row: dict) -> str:
    if row.get("delivery_date"):
        return f"delivered {row['delivery_date']}"
    return f"ETA {row.get('estimated_delivery') or 'unknown'}"


def _compare_shipments_answer(rows: list, tracking_ids: list) -> dict:
    """Deterministic (zero-LLM) side-by-side comparison — the answer for a
    plain 'compare X and Y' / 'show me both' question. Causal comparisons
    ('which is more delayed and WHY') are routed to Stage 7 v1 synthesis
    instead by _compare_shipments() below, since explaining a difference
    needs real reasoning this fixed formatter can't produce — this only
    ever has to list facts, not explain them."""
    found_ids = {r["tracking_id"] for r in rows}
    missing_ids = [tid for tid in tracking_ids if tid not in found_ids]

    if not rows:
        return {
            "answer": f"I couldn't find any shipments matching {', '.join(tracking_ids)}.",
            "confidence_score": 0.3,
            "supporting_data": [],
        }

    lines = []
    for row in rows:
        delay_note = (
            f", delayed ({row['reason_for_delay']})"
            if row.get("reason_for_delay", "NONE") != "NONE" else ""
        )
        lines.append(
            f"- {row['tracking_id']}: {row['current_status']}{delay_note}, "
            f"{_fmt_loc_or_date(row)}, {row.get('open_issue_count', 0)} open issue(s)"
        )

    answer = "Here's a comparison:\n" + "\n".join(lines)
    if missing_ids:
        answer += f"\n\n(Couldn't find: {', '.join(missing_ids)})"

    return {
        "answer": answer,
        "confidence_score": 1.0 if not missing_ids else 0.7,
        "supporting_data": rows,
    }


_COMPARE_SQL = """
    SELECT tracking_id, current_status, reason_for_delay, delay_comments,
           estimated_delivery, delivery_date, open_issue_count, is_international,
           customs_status
    FROM v_shipment_journey_summary
    WHERE tracking_id = ANY(%(tracking_ids)s)
"""
_COMPARE_ENTITY_KEYS = ["v_shipment_journey_summary"]


def _compare_shipments(query: str, extracted, elapsed_ms, session_id):
    """Multi-shipment comparison — closes the gap flagged as still-open in
    AGENTIC_RAG_ARCHITECTURE.md §9 ("closing it means teaching the multi-ID
    guard to route into cross-result synthesis instead of always
    declining"). The SQL is always hand-built here (tracking_id = ANY(...))
    — never LLM-drafted, since the shape is fully known and deterministic —
    but it still passes through Stage 5's guardrail like every other query
    in this system, no exceptions (defense in depth from day one, regardless
    of source). Only the ANSWER differs by question type:
      - a plain "compare X and Y" gets a free, instant side-by-side (no LLM)
      - a causal comparison ("which is more delayed and WHY") routes through
        Stage 7 v1 synthesis instead, reusing the exact is_causal_query
        signal already used for the single-shipment causal gate — same
        family of fix, same reasoning: a fixed formatter can list facts but
        can't explain a difference.
    `source`/`llm_provider`/`llm_model` for the audit row are decided by
    which branch actually ran, NOT by how the SQL was generated (it's always
    "template"-style here) — this is what v_chat_llm_usage's cost signal
    actually needs to know: did this interaction spend a real LLM call.
    """
    try:
        validated_sql = guardrails.validate_sql(_COMPARE_SQL, _COMPARE_ENTITY_KEYS)
    except guardrails.GuardrailError as exc:
        yield {"stage": "sql_rejected", "detail": {"reason": str(exc)}}
        yield from _finish(query, extracted, "compare_shipments", REJECTED_ANSWER, [],
                           elapsed_ms=elapsed_ms(), session_id=session_id)
        return

    params = {"tracking_ids": extracted.tracking_ids}
    yield {"stage": "sql_generated", "detail": {
        "sql": validated_sql, "params": params, "source": "template",
    }}
    yield {"stage": "sql_validated", "detail": {"status": "accepted"}}
    yield {"stage": "executing", "detail": {}}

    try:
        result = executor.execute_query(validated_sql, params)
    except executor.ExecutionError as exc:
        yield {"stage": "execution_failed", "detail": {"reason": str(exc)}}
        yield from _finish(query, extracted, "compare_shipments", EXECUTION_FAILED_ANSWER, [],
                           source="template", guardrail_status="accepted", elapsed_ms=elapsed_ms(),
                           session_id=session_id)
        return
    yield {"stage": "rows_returned", "detail": {
        "count": result.row_count, "elapsed_ms": result.elapsed_ms,
    }}

    if schema_scope.is_causal_query(query):
        yield {"stage": "causal_comparison_needs_llm", "detail": {
            "reason": "comparison question asks 'why' — routing to Stage 7 v1 synthesis for "
                      "real cross-shipment reasoning instead of a plain side-by-side listing",
        }}
        answer = synthesize.synthesize(query, result.rows, sql=validated_sql, params=params)
        llm_provider, llm_model = llm_client.active_model_info()
        yield from _finish(query, extracted, "compare_shipments", answer.model_dump(), result.rows,
                           source="llm", llm_provider=llm_provider, llm_model=llm_model,
                           guardrail_status="accepted", elapsed_ms=elapsed_ms(), session_id=session_id)
    else:
        answer = _compare_shipments_answer(result.rows, extracted.tracking_ids)
        yield from _finish(query, extracted, "compare_shipments", answer, result.rows,
                           source="template", guardrail_status="accepted", elapsed_ms=elapsed_ms(),
                           session_id=session_id)


def _multi_org_answer(org_names: list) -> dict:
    # Mirrors the (now-closed) multi-tracking-id decline's original shape —
    # same failure class (a query naming more than one real entity, where
    # entities.py's fuzzy matcher only returns/orders ALL matches, and
    # answering about just the first would be misleading), same fix: decline
    # and ask which one, instead of silently picking org_names[0]. Discovered
    # via CHAT_TEST_QUERIES.md §6's "compare Acme Corp and Globex" example,
    # which turned out NOT to be a safely-declined non-goal before this guard
    # existed. Unlike tracking_id, this doesn't (yet) route into a real
    # comparison — comparing customer portfolios is a different, bigger
    # feature than comparing two shipments' status fields side by side.
    names_str = " and ".join(org_names) if len(org_names) == 2 else ", ".join(org_names)
    return {
        "answer": (
            f"I can look up one customer at a time right now — you mentioned {names_str}. "
            "Which one would you like details on? (Comparing multiple customers in a "
            "single answer isn't supported yet.)"
        ),
        "confidence_score": 0.0,
        "supporting_data": {"org_names_found": org_names},
    }


def _classify_and_extract(query: str):
    # Stage 1 and Stage 2 only need the raw query text — no dependency on
    # each other — so they run concurrently (AGENTIC_RAG_ARCHITECTURE.md §3).
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        intent_future = pool.submit(intent_stage.classify_intent, query)
        entities_future = pool.submit(entity_stage.extract_entities, query)
        return intent_future.result(), entities_future.result()


def _finish(
    query: str, extracted, intent_name, answer_payload: dict, context_rows,
    *, source=None, llm_provider=None, llm_model=None, guardrail_status=None, elapsed_ms=None,
    session_id=None,
):
    # Audit write happens BEFORE the final yield, not after, so it still
    # runs even if the caller stops iterating right after "answer_ready".
    # source/guardrail_status/etc. default to None for the decline paths
    # (multi-org guard, no_intent_match) that never generated SQL at all —
    # see shipment_chat_log's column comments in 01_phase1_schema.sql.
    # session_id is persisted regardless of outcome (even a decline) so a
    # NEXT turn in the same session has something to look up — see
    # session.py's get_last_tracking_id().
    log_chat_interaction(
        tracking_id=extracted.tracking_id,
        customer_id=None,  # no auth/session model in Phase 1
        user_query=query,
        ai_response=answer_payload.get("answer", ""),
        context_snapshot={"intent": intent_name, "rows": context_rows},
        confidence_score=answer_payload.get("confidence_score"),
        source=source,
        llm_provider=llm_provider,
        llm_model=llm_model,
        guardrail_status=guardrail_status,
        latency_ms=elapsed_ms,
        session_id=session_id,
    )
    yield {"stage": "answer_ready", "detail": answer_payload}


def run_pipeline(query: str, session_id: str | None = None):
    start = time.monotonic()
    elapsed_ms = lambda: round((time.monotonic() - start) * 1000, 1)  # noqa: E731

    # nlu_ms/scoping_ms (below) are Phase 4's monitoring half — see
    # AGENTIC_RAG_ARCHITECTURE.md §24: no vector-index backend swap is
    # justified yet at today's scale (28 templates / 16 entities, ~10-25ms
    # combined), but the trigger for revisiting that decision should be a
    # measured trend over real traffic, not another one-off manual check.
    # Only in the verbose/OPS trace, not persisted — this is an ops-debugging
    # signal, not a per-interaction fact worth a shipment_chat_log column.
    _t0 = time.monotonic()
    intent_result, extracted = _classify_and_extract(query)
    nlu_ms = round((time.monotonic() - _t0) * 1000, 2)

    # Phase 3 session memory (chat/session.py) — deliberately narrow: only
    # ever carries forward a tracking_id, and only for a short, entity-less,
    # pronoun-shaped follow-up ("what about it") with no fleet-wide language.
    # A confidently WRONG carried-forward entity is worse than no session
    # memory at all, so this stays conservative rather than trying to catch
    # every possible follow-up phrasing — see session.py's module docstring.
    session_context_used = False
    if session_id and session_stage.wants_session_context(query, extracted):
        inherited_tracking_id = session_stage.get_last_tracking_id(session_id)
        if inherited_tracking_id:
            extracted.tracking_id = inherited_tracking_id
            extracted.tracking_ids = [inherited_tracking_id]
            session_context_used = True

    yield {"stage": "intent_classified", "detail": {
        "intent": intent_result.intent,
        "confidence": round(intent_result.confidence, 3),
        "nlu_ms": nlu_ms,
    }}
    yield {"stage": "entities_extracted", "detail": {
        "tracking_id": extracted.tracking_id,
        "tracking_ids": extracted.tracking_ids,
        "enum_matches": extracted.enum_matches,
        "org_name": extracted.org_name,
        "dates": extracted.dates,
        "session_context_used": session_context_used,
    }}

    if len(extracted.tracking_ids) > 1:
        # Multi-shipment comparison (AGENTIC_RAG_ARCHITECTURE.md §9's
        # originally-flagged gap, closed) — answering about only the first
        # (which fill_template would silently do) is still a worse failure
        # than this, so it's checked before normal routing; the difference
        # from the original design is what happens next isn't a decline
        # anymore, see _compare_shipments().
        yield {"stage": "multiple_tracking_ids_detected", "detail": {
            "reason": "comparing shipments via tracking_id = ANY(...) instead of answering "
                      "about only the first or declining outright",
            "tracking_ids": extracted.tracking_ids,
        }}
        yield from _compare_shipments(query, extracted, elapsed_ms, session_id)
        return

    if len(extracted.org_names) > 1:
        # Same reasoning as the multi-tracking-id guard immediately above,
        # for the identical failure shape with customer names instead of
        # tracking IDs — see _multi_org_answer's docstring.
        yield {"stage": "multiple_org_names_detected", "detail": {
            "reason": "no v0 template supports comparing multiple customers; "
                      "answering about only the first would be misleading",
            "org_names": extracted.org_names,
        }}
        yield from _finish(query, extracted, None, _multi_org_answer(extracted.org_names), [],
                           elapsed_ms=elapsed_ms(), session_id=session_id)
        return

    _t0 = time.monotonic()
    scoped = schema_scope.scope_schema(query, extracted)
    scoping_ms = round((time.monotonic() - _t0) * 1000, 2)
    yield {"stage": "schema_scoped", "detail": {
        "entities": scoped.entities,
        "scores": {k: round(v, 3) for k, v in scoped.scores.items()},
        "forced_entities": scoped.forced_entities,
        "scoping_ms": scoping_ms,
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
        elif (sql_templates.TEMPLATES[resolved_intent].is_aggregate
              and schema_scope.wants_different_grouping(query, resolved_intent)):
            # Live bug: "group shipments by package type and show how many are delayed" —
            # the exact query AGENTIC_RAG_ARCHITECTURE.md §9 originally used to demonstrate
            # Stage 4b's GROUP BY capability — now confidently matches delay_reason_breakdown
            # (a zero-param aggregate that groups by reason_for_delay) and silently answers
            # a breakdown by REASON instead, never once looking at "package type". Same
            # family as the causal/list/history gates above: a successfully-filled AGGREGATE
            # template is not the right answer if it aggregates along the wrong axis,
            # regardless of how confidently it matched — this is the "wrong axis" case,
            # distinct from list_query_needs_llm's "wrong shape entirely (wants individual
            # records)" case above.
            yield {"stage": "wrong_grouping_needs_llm", "detail": {
                "reason": "the query explicitly asks to group/break down by a dimension the "
                          "matched aggregate template doesn't group by — routing to Stage 4b "
                          "instead of returning a differently-grouped breakdown",
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
            yield from _finish(query, extracted, resolved_intent, CLARIFYING_ANSWER, [],
                               elapsed_ms=elapsed_ms(), session_id=session_id)
            return

    yield {"stage": "sql_generated", "detail": {
        "sql": filled.sql.strip(), "params": filled.params, "source": filled.source,
    }}

    # Only meaningful when Stage 4b actually drafted this SQL — a template
    # fill has no provider/model to report. Computed once here so every
    # _finish() call below (rejected, execution-failed, or successful) tags
    # the audit row consistently — see shipment_chat_log's llm_provider/
    # llm_model columns (01_phase1_schema.sql) and v_chat_llm_usage, the
    # dashboard view this data exists to back.
    llm_provider = llm_model = None
    if filled.source == "llm":
        llm_provider, llm_model = llm_client.active_model_info()

    guardrail_error = None
    validated_sql = None
    try:
        validated_sql = guardrails.validate_sql(filled.sql, filled.entity_keys)
    except guardrails.GuardrailError as exc:
        guardrail_error = exc

    if guardrail_error is not None and filled.source == "llm":
        # Escalation ladder — named at design time in AGENTIC_RAG_ARCHITECTURE.md
        # §4 Stage 4b ("escalate to a stronger model only if Stage 5 rejects the
        # SQL twice in a row") but never actually built until now. A rejected
        # LLM-drafted query gets exactly ONE retry, with the rejection reason
        # and the failed SQL fed back into the prompt so the model can correct
        # its actual mistake instead of blindly repeating it — optionally on a
        # different/stronger model if AGENT_LLM_ESCALATION_MODEL is set (unset
        # by default: the retry then just reuses the same model with feedback,
        # still strictly better than no feedback loop at all). A rejected
        # TEMPLATE's SQL is never retried this way — that's a bug in hand-
        # written SQL, not something a prompt retry could fix.
        yield {"stage": "sql_rejected_retrying", "detail": {
            "reason": str(guardrail_error),
            "escalation_model": llm_client.ESCALATION_MODEL,
        }}
        retry_filled = sql_llm.draft_sql(
            query, scoped.entities, extracted,
            previous_sql=filled.sql, retry_context=str(guardrail_error),
            model_override=llm_client.ESCALATION_MODEL,
        )
        if retry_filled is not None:
            try:
                validated_sql = guardrails.validate_sql(retry_filled.sql, retry_filled.entity_keys)
                guardrail_error = None
                filled = retry_filled
                llm_provider, llm_model = llm_client.active_model_info(llm_client.ESCALATION_MODEL)
                yield {"stage": "sql_generated", "detail": {
                    "sql": filled.sql.strip(), "params": filled.params,
                    "source": filled.source, "retry": True,
                }}
            except guardrails.GuardrailError as exc:
                guardrail_error = exc

    if guardrail_error is not None:
        yield {"stage": "sql_rejected", "detail": {"reason": str(guardrail_error)}}
        yield from _finish(query, extracted, resolved_intent, REJECTED_ANSWER, [],
                           source=filled.source, llm_provider=llm_provider, llm_model=llm_model,
                           guardrail_status="rejected", elapsed_ms=elapsed_ms(), session_id=session_id)
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
        yield from _finish(query, extracted, resolved_intent, EXECUTION_FAILED_ANSWER, [],
                           source=filled.source, llm_provider=llm_provider, llm_model=llm_model,
                           guardrail_status="accepted", elapsed_ms=elapsed_ms(), session_id=session_id)
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
    yield from _finish(query, extracted, audit_intent, answer.model_dump(), result.rows,
                       source=filled.source, llm_provider=llm_provider, llm_model=llm_model,
                       guardrail_status="accepted", elapsed_ms=elapsed_ms(), session_id=session_id)
