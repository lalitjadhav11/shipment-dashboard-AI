"""
Phase A trajectory-eval golden set.

Mechanically this is the exact same trace-based verification
test_intent_and_routing.py/test_compare_shipments.py already do by hand
(consume_until() + asserting on pipeline.py's trace stages) — nothing new is
being invented here. The DIFFERENCE from tests/ is purpose and growth path:
tests/ pins one regression test to one historic bug/commit and lives next to
the code it protects; this file is a curated, growing PRODUCT quality
baseline — realistic scenarios collected in one place on purpose, including
ones promoted later from real production misses (see AGENTIC_RAG_ARCHITECTURE.md's
Phase C notes on shipment_chat_log.confidence_score). Zero LLM cost, zero
live DB — every case either stops consume_until() right at the routing
decision itself (before Stage 6/7 would ever run), or supplies canned
executor.execute_query() rows for the handful of cases (multi-shipment
comparison) whose routing decision only becomes visible after a DB fetch.
"""
from dataclasses import dataclass, field

# Canned Stage-6 output for comparison-routing cases — same shape as
# test_compare_shipments.py's TWO_SHIPMENT_ROWS, tracking_ids adjusted to
# match this file's own golden queries. Content only matters for the plain
# (non-causal) comparison case, which runs _compare_shipments_answer() for
# real; the causal case stops before synthesize() would ever read it.
TWO_SHIPMENT_ROWS = [
    {
        "tracking_id": "700000000001", "current_status": "IN_TRANSIT",
        "reason_for_delay": "NONE", "delay_comments": None,
        "estimated_delivery": "2026-08-15T00:00:00Z", "delivery_date": None,
        "open_issue_count": 0, "is_international": False, "customs_status": "NOT_REQUIRED",
    },
    {
        "tracking_id": "100000000034", "current_status": "CUSTOMS_HOLD",
        "reason_for_delay": "CUSTOMS", "delay_comments": "Missing HS code.",
        "estimated_delivery": "2026-08-10T00:00:00Z", "delivery_date": None,
        "open_issue_count": 1, "is_international": True, "customs_status": "HELD",
    },
]


@dataclass
class TrajectoryCase:
    id: str                                   # short slug, shown as the pytest id
    query: str
    expect_stage: str                         # trace stage that MUST fire
    expect_forced_entities: list = field(default_factory=list)
    must_not_fire: list = field(default_factory=list)   # regression guard
    db_rows: list | None = None               # canned executor.execute_query rows —
    # only needed for cases (comparison) whose routing decision happens after
    # a Stage 6 fetch; leave None to keep the case DB-free
    notes: str = ""


CASES = [
    TrajectoryCase(
        id="causal_customs_blockers",
        query="why are so many orders held at customs",
        expect_stage="causal_query_needs_llm",
        expect_forced_entities=["shipment_issue"],
        must_not_fire=["sql_generated"],
        notes="§15/§15.1 — a fillable-but-non-explanatory template must not win a 'why' question",
    ),
    TrajectoryCase(
        id="wrong_grouping_axis",
        query="group shipments by package type and show how many are delayed",
        expect_stage="wrong_grouping_needs_llm",
        must_not_fire=["sql_generated"],
        notes="§9 origin story / Phase 4 finding — this exact query silently regressed to the "
              "wrong template (delay_reason_breakdown) once; guards against that regressing again",
    ),
    TrajectoryCase(
        id="list_style_two_filters",
        query="show me all custom shipments those are impacted due to weather delay",
        expect_stage="list_query_needs_llm",
        must_not_fire=["sql_generated"],
        notes="§18 — a zero-param aggregate is not the right answer to a 'show me all X' question",
    ),
    TrajectoryCase(
        id="history_timeline_request",
        query="give me the status history for 800000000073",
        expect_stage="history_query_needs_llm",
        expect_forced_entities=["v_shipment_journey_summary", "tracking_event"],
        must_not_fire=["causal_query_needs_llm", "sql_generated"],
        notes="§22 — a template reporting only the LAST hop is not an answer to a 'history' question",
    ),
    TrajectoryCase(
        id="comparison_causal",
        query="which of 700000000001 and 100000000034 is more delayed and why",
        expect_stage="causal_comparison_needs_llm",
        db_rows=TWO_SHIPMENT_ROWS,
        notes="Phase 3 — a causal comparison must route to real Stage 7v1 reasoning, not the "
              "free side-by-side formatter",
    ),
    TrajectoryCase(
        id="comparison_plain_stays_deterministic",
        query="Compare 700000000001 and 100000000034",
        expect_stage="answer_ready",
        db_rows=TWO_SHIPMENT_ROWS,
        must_not_fire=["causal_comparison_needs_llm"],
        notes="Negative control — a plain side-by-side comparison (no causal wording) must stay "
              "on the free deterministic formatter, never spend an LLM call",
    ),
    TrajectoryCase(
        id="plain_status_lookup_stays_deterministic",
        query="Where is tracking number 700000000001 right now?",
        expect_stage="sql_generated",
        must_not_fire=[
            "causal_query_needs_llm", "wrong_grouping_needs_llm",
            "history_query_needs_llm", "list_query_needs_llm",
        ],
        notes="Negative control — an ordinary single-shipment lookup must stay on the free "
              "Stage 4a template path, never reach for any of the Stage 4b gates",
    ),
]
