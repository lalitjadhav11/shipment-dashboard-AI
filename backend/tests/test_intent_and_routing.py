"""
Golden-query regression suite — the executable form of CHAT_TEST_QUERIES.md.

Every value asserted here was verified live against the running embedding
model (docker compose exec backend python ...) before being written down,
the same offline `intent.rank_intents()` technique
AGENTIC_RAG_ARCHITECTURE.md's own methodology notes (§12) describe using by
hand — this file just makes that check permanent and automatic instead of
something someone has to remember to redo.

Two kinds of tests:
  1. Intent classification only (fast, no pipeline/DB/LLM involved) — one
     query per template, confirming it still resolves to the right intent
     with a comfortable margin above CONFIDENCE_THRESHOLD.
  2. Full pipeline routing (via consume_until, see conftest.py) for the
     documented edge cases — multi-ID decline, bare-ID default, the
     fleet-intent override guard, and the causal/aggregate "successfully-
     filled template is not necessarily the RIGHT one" gates. These stop the
     pipeline generator right after the routing decision is visible in the
     trace, before Stage 6 (DB) or a real Stage 4b LLM call would ever fire.
"""
import pytest

from chat import intent as intent_stage
from chat import pipeline

from conftest import consume_until

# --- Part 1: one canonical query per template, intent classification only --

GOLDEN_INTENT_QUERIES = [
    # Single-shipment lookups (tracking_id required)
    ("Where is tracking number 700000000001 right now?", "where_is_my_package"),
    ("Why is my shipment 800000000010 delayed?", "why_is_it_late"),
    ("Who is the customer for tracking number 700000000001?", "shipment_customer_lookup"),
    ("What delivery service is tracking number 700000000001 using?", "shipment_package_details"),
    ("Where is my shipment 700000000001 coming from and where is it going?", "shipment_route"),
    ("When will my package 700000000001 be picked up, and what's the delivery window?", "shipment_schedule"),
    ("Has tracking number 400000000019 had any failed delivery attempts?", "shipment_delivery_attempts"),
    ("Is tracking number 700000000001 held in customs right now?", "customs_status"),
    ("What is the issue with tracking number 900000000005?", "open_issues_for_shipment"),
    # Fleet-wide dashboards (zero-param)
    ("Give me the dashboard headline numbers", "dashboard_headline"),
    ("Give me a breakdown of shipment statuses", "status_breakdown"),
    ("How is our on-time delivery performance?", "ontime_performance"),
    ("What are the top reasons shipments are delayed?", "delay_reason_breakdown"),
    ("How many domestic versus international shipments do we have?", "domestic_vs_international_split"),
    ("Show me shipment volume trend over the last two weeks", "daily_volume_trend"),
    ("What's our service level mix across all shipments?", "service_level_mix"),
    ("How much chat activity have we had, and what's the average confidence?", "chat_activity_summary"),
    ("Give me today's critical shipment issues", "ops_daily_briefing"),
    ("Show me top customers by volume", "top_customers_by_volume"),
    # Mix-and-match / reverse lookups
    ("Show me all shipments for Smith Ltd", "shipments_by_customer"),
    ("Which of Walker PLC's shipments are currently delayed?", "shipments_by_customer_delayed"),
    ("Give me 5 shipments that currently have status customs hold", "shipments_by_status"),
    ("Show me all pallet shipments", "shipments_by_package_type"),
    ("Show me all express delivery shipments", "shipments_by_delivery_type"),
    ("Which shipments have had failed delivery attempts?", "failed_delivery_shipments"),
    ("Which shipments are going to or coming from Seattle?", "shipments_by_location"),
    ("Show me all our large and extra-large shipments", "shipments_by_package_size"),
    ("Which shipments are scheduled for pickup on July 17th?", "shipments_by_pickup_date"),
]


@pytest.mark.parametrize("query,expected_intent", GOLDEN_INTENT_QUERIES, ids=[q for q, _ in GOLDEN_INTENT_QUERIES])
def test_golden_query_resolves_to_expected_intent(query, expected_intent):
    result = intent_stage.classify_intent(query)
    assert result.intent == expected_intent, (
        f"expected {expected_intent!r} but got {result.intent!r} "
        f"(confidence={result.confidence:.3f}); top-3: "
        f"{[(r.intent, round(r.score, 3)) for r in intent_stage.rank_intents(query)[:3]]}"
    )


# --- Part 2: documented near-miss — NOT a bug, a known §16.4 trade-off -----

def test_casual_open_issues_phrasing_falls_below_threshold():
    """"Are there any open issues on X" (the pre-§16.4 example style) scores
    ~0.37 against the CURRENT open_issues_for_shipment wording — verified
    live, matching the exact number AGENTIC_RAG_ARCHITECTURE.md §16.4 itself
    recorded (0.373) when it reworded the example to fix a different,
    higher-priority query ("what's the issue with X"). This is a known,
    accepted trade-off, not a regression — this test exists so a FUTURE
    reword that pushes the score even lower (or higher, fixing it for free)
    is a visible, deliberate change instead of a silent one."""
    result = intent_stage.classify_intent("Are there any open issues on 900000000005?")
    assert result.intent is None
    assert result.confidence < intent_stage.CONFIDENCE_THRESHOLD


# --- Part 3: full-pipeline routing edge cases -------------------------------

def test_multiple_tracking_ids_are_detected_for_comparison():
    # Detection only — see test_compare_shipments.py for what happens next
    # (Phase 3: routes to a real comparison, not a decline anymore).
    events = consume_until(
        pipeline.run_pipeline("Compare 700000000001 and 100000000002"),
        "multiple_tracking_ids_detected",
    )
    assert events[-1]["stage"] == "multiple_tracking_ids_detected"
    assert events[-1]["detail"]["tracking_ids"] == ["700000000001", "100000000002"]


def test_bare_tracking_id_defaults_to_where_is_my_package():
    # consume_until only needs "sql_generated" as the stop condition — it
    # collects every event along the way, so "intent_defaulted" (which fires
    # earlier in the trace) is still visible in the full list.
    events = consume_until(pipeline.run_pipeline("800000000131"), "sql_generated")
    stages = [e["stage"] for e in events]
    assert "intent_defaulted" in stages
    sql_events = [e for e in events if e["stage"] == "sql_generated"]
    assert sql_events and "v_shipment_journey_summary" in sql_events[0]["detail"]["sql"]


def test_specific_question_with_tracking_id_skips_blind_default():
    # Regression for §11 — "what was the previous stage of X" must NOT be
    # force-answered by where_is_my_package (which only reports CURRENT
    # status); it should fall through toward Stage 4b instead.
    events = consume_until(
        pipeline.run_pipeline("What was the previous stage of 100000000002?"),
        "default_skipped_too_specific", "llm_sql_fallback_attempting",
    )
    stages = [e["stage"] for e in events]
    assert "default_skipped_too_specific" in stages
    assert "intent_defaulted" not in stages


def test_fleet_intent_overridden_when_tracking_id_present():
    # Regression for the original corner-case audit (§9) — a fleet-wide,
    # zero-param intent (top_customers_by_volume) confidently matching
    # despite a tracking_id being present must be overridden to a
    # shipment-scoped lookup, not answered as an irrelevant fleet report.
    events = consume_until(
        pipeline.run_pipeline("top customers 700000000001"),
        "intent_overridden", "sql_generated",
    )
    overridden = [e for e in events if e["stage"] == "intent_overridden"]
    assert overridden and overridden[0]["detail"]["overridden_to"] == "where_is_my_package"


def test_causal_query_discards_non_explanatory_template_match():
    # Regression for §15 — "why...customs" confidently matches
    # shipments_by_status (a fillable but non-explanatory list template);
    # the causal gate must discard that fill and route toward Stage 4b
    # instead of returning a bare tracking-ID list that never says why.
    events = consume_until(
        pipeline.run_pipeline("Why are so many orders held at customs?"),
        "causal_query_needs_llm", "sql_generated",
    )
    stages = [e["stage"] for e in events]
    assert "causal_query_needs_llm" in stages
    assert "sql_generated" not in stages  # never fell back to the discarded template's SQL


def test_wrong_grouping_axis_discarded_for_mismatched_aggregate():
    # Regression for a bug found live: this is the EXACT query
    # AGENTIC_RAG_ARCHITECTURE.md §9 originally used to demonstrate Stage
    # 4b's GROUP BY capability. It later started confidently matching
    # delay_reason_breakdown (groups by reason_for_delay) and silently
    # answering a breakdown by REASON instead of by package type. The gate
    # must discard that fill and route toward Stage 4b, the same pattern as
    # the causal/list/history gates.
    events = consume_until(
        pipeline.run_pipeline("group shipments by package type and show how many are delayed"),
        "wrong_grouping_needs_llm", "sql_generated",
    )
    stages = [e["stage"] for e in events]
    assert "wrong_grouping_needs_llm" in stages
    assert "sql_generated" not in stages


def test_aggregate_match_discarded_for_list_style_question():
    # Regression for §18 — a "show me all X shipments" question with two
    # filters (package_type + reason_for_delay) confidently matches
    # delay_reason_breakdown (a zero-param aggregate); the gate must discard
    # it since no template combines those two filters, rather than silently
    # returning an aggregate no one asked for.
    events = consume_until(
        pipeline.run_pipeline("show me all custom shipments those are impacted due to weather delay"),
        "list_query_needs_llm", "sql_generated",
    )
    stages = [e["stage"] for e in events]
    assert "list_query_needs_llm" in stages
    assert "sql_generated" not in stages


def test_numeric_threshold_query_falls_through_to_stage_4b():
    # "shipments weighing more than 20kg" DOES confidently classify to
    # shipments_by_package_size (~0.52) — Stage 1 alone doesn't decline this.
    # The correctness guarantee is one stage later: no enum value fuzzy-
    # matches "20kg"/"weighing more than", so the template's required
    # `package_size` entity is never populated, Stage 4a fails to fill, and
    # it correctly falls through instead of misinterpreting the number as an
    # enum. Verified live before writing this assertion.
    from chat import entities as entity_stage
    extracted = entity_stage.extract_entities("shipments weighing more than 20kg")
    assert "package_size" not in extracted.enum_matches

    events = consume_until(
        pipeline.run_pipeline("shipments weighing more than 20kg"),
        "sql_generation_failed", "sql_generated",
    )
    stages = [e["stage"] for e in events]
    assert "sql_generation_failed" in stages
    assert "sql_generated" not in stages


def test_out_of_domain_query_does_not_match_any_template():
    result = intent_stage.classify_intent("what's the weather today")
    assert result.intent is None


def test_multi_customer_comparison_declines_without_guessing(monkeypatch):
    # Regression for a gap found while building this suite (not previously
    # documented): "compare Acme Corp and Globex" used to extract only ONE
    # org_name (entities.py's _extract_org_name used process.extractOne, a
    # single best match), so shipments_by_customer_delayed filled
    # confidently and silently answered about only the first company —
    # CHAT_TEST_QUERIES.md §6 wrongly listed this as a safely-declined
    # non-goal. Fixed by extending the same multi-tracking-id guard pattern
    # (AGENTIC_RAG_ARCHITECTURE.md §9) to org_name via the new
    # multiple_org_names_detected event.
    import time
    from chat import entities as entity_stage

    monkeypatch.setitem(entity_stage._ORG_NAME_CACHE, "names", ["Acme Corp", "Globex"])
    monkeypatch.setitem(entity_stage._ORG_NAME_CACHE, "loaded_at", time.monotonic())

    events = consume_until(
        pipeline.run_pipeline("compare Acme Corp and Globex"),
        "multiple_org_names_detected", "sql_generated",
    )
    stages = [e["stage"] for e in events]
    assert "multiple_org_names_detected" in stages
    assert "sql_generated" not in stages
