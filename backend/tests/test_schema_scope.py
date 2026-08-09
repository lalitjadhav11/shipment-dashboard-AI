"""
Stage 3 gate-function regression tests (is_causal_query / wants_history /
wants_individual_records) — pure regex, no embedding model needed.

These three detectors have been the site of repeated, narrowly-scoped
regressions (AGENTIC_RAG_ARCHITECTURE.md §16.1/§16.2/§16.3/§18): each fix
widened a word family or tightened a false-positive boundary, verified only
by hand against a true-positive/true-negative list documented in prose. This
file turns those exact documented lists into assertions, so the next wording
tweak gets checked against ALL of them automatically, not just the one query
that motivated the change (which is precisely how §16.1 -> §16.2 -> §16.3
each shipped without regressing the others, but only because someone
remembered to re-check by hand every time).
"""
import pytest

from chat import schema_scope


# --- is_causal_query: true positives (§15, §16.1, §16.2, §16.3) -------------

CAUSAL_TRUE_POSITIVES = [
    "Why are so many orders held at customs?",
    "What are the major blockers for international packages?",
    "What is causing failed deliveries?",  # §16.2 — gerund "causing"
    "What is the root cause of our open issues?",
    "what is hindering our deliveries",
    "what is preventing on-time delivery",
    "shipments obstructing the delivery pipeline",
    "What does the 'OTHER' category of delays include?",  # §16.3 — different shape, same fix
    "What falls under the WEATHER delay reason?",
    "What makes up the OTHER issue type?",
]

# --- is_causal_query: true negatives — must NOT be treated as causal --------

CAUSAL_TRUE_NEGATIVES = [
    "shipments blocked from delivery",  # adjective describing state, not a cause request
    "held up at the hub",
    "Give me a breakdown of shipment statuses",
    "Is my shipment held in customs?",
    "Where is tracking number 700000000001?",
    "Show me all express delivery shipments",
]


@pytest.mark.parametrize("query", CAUSAL_TRUE_POSITIVES)
def test_causal_query_true_positive(query):
    assert schema_scope.is_causal_query(query) is True


@pytest.mark.parametrize("query", CAUSAL_TRUE_NEGATIVES)
def test_causal_query_true_negative(query):
    assert schema_scope.is_causal_query(query) is False


# --- wants_history --------------------------------------------------------

HISTORY_TRUE_POSITIVES = [
    "give me the status history for 800000000073",
    "what's the journey timeline for this shipment",
    "what was the previous stage of 100000000002",
    "show me tracking events for this package",
]

HISTORY_TRUE_NEGATIVES = [
    "Where is tracking number 700000000001 right now?",
    "Why is my shipment delayed?",
    "Show me all pallet shipments",
]


@pytest.mark.parametrize("query", HISTORY_TRUE_POSITIVES)
def test_wants_history_true_positive(query):
    assert schema_scope.wants_history(query) is True


@pytest.mark.parametrize("query", HISTORY_TRUE_NEGATIVES)
def test_wants_history_true_negative(query):
    assert schema_scope.wants_history(query) is False


# --- wants_individual_records: the §18 aggregate-noun negative lookahead ---

LIST_TRUE_POSITIVES = [
    "give me 5 shipments those are at customs",
    "show me shipments with status delivered",
    "list shipments that are lost",
    "which shipments are going to Seattle",
]

LIST_TRUE_NEGATIVES = [
    # §18: "shipment(s)" immediately followed by an aggregate noun must NOT
    # be treated as a request for individual records.
    "Give me a breakdown of shipment statuses",
    "shipment status breakdown",
    "shipment performance trend",
    "shipment volume summary",
]


@pytest.mark.parametrize("query", LIST_TRUE_POSITIVES)
def test_wants_individual_records_true_positive(query):
    assert schema_scope.wants_individual_records(query, extracted=None) is True


@pytest.mark.parametrize("query", LIST_TRUE_NEGATIVES)
def test_wants_individual_records_true_negative(query):
    assert schema_scope.wants_individual_records(query, extracted=None) is False


# --- scope_schema(): forced-entity mechanics (needs the embedding index) --

def test_causal_query_forces_shipment_issue_into_scope():
    scoped = schema_scope.scope_schema("Why are so many orders held at customs?")
    assert "shipment_issue" in scoped.entities
    assert "shipment_issue" in scoped.forced_entities


def test_history_query_forces_journey_entities_into_scope():
    scoped = schema_scope.scope_schema("give me details about 400000000014 and their history of status")
    assert (
        "v_shipment_journey_summary" in scoped.entities
        or "tracking_event" in scoped.entities
    )


def test_non_causal_non_history_query_forces_nothing():
    scoped = schema_scope.scope_schema("Show me all pallet shipments")
    assert scoped.forced_entities == [] or "shipment_issue" not in scoped.forced_entities


# --- shipment_chat_log must NEVER be scoped in, regardless of how it scores

CHAT_LOG_PROBE_QUERIES = [
    # Verified live before this fix: shipment_chat_log scored into the
    # top-4 for every one of these, including the exact example_nl for the
    # legitimate chat_activity_summary intent.
    "what have people been asking the AI chat and how confident were the answers",
    "show me recent chat questions and answers",
    "what questions has the chat been getting from customers",
    "how is the AI chat feature being used",
    "give me the chat activity summary",
]


@pytest.mark.parametrize("query", CHAT_LOG_PROBE_QUERIES)
def test_shipment_chat_log_never_scoped_in(query):
    scoped = schema_scope.scope_schema(query)
    assert "shipment_chat_log" not in scoped.entities
    assert "shipment_chat_log" not in scoped.scores
    assert "shipment_chat_log" not in scoped.forced_entities


# --- wants_different_grouping: the "right shape, wrong axis" gate --------

@pytest.mark.parametrize("query,resolved_intent", [
    ("group shipments by package type and show how many are delayed", "delay_reason_breakdown"),
    ("break down shipments by package size", "status_breakdown"),
    ("split shipments by customer", "delay_reason_breakdown"),
    ("group my shipments by scope", "status_breakdown"),
])
def test_wants_different_grouping_true_positive(query, resolved_intent):
    assert schema_scope.wants_different_grouping(query, resolved_intent) is True


@pytest.mark.parametrize("query,resolved_intent", [
    # No group/split signal word at all — must never fire on an ordinary
    # "by X" phrase that isn't a grouping request.
    ("Show me top customers by volume", "top_customers_by_volume"),
    ("What's our service level mix across all shipments?", "service_level_mix"),
    ("Give me a breakdown of shipment statuses", "status_breakdown"),
    # Group signal present, but the requested dimension matches what the
    # resolved intent's template actually groups by — not a mismatch.
    ("Give me a breakdown grouped by status", "status_breakdown"),
    ("Group shipments by delay reason", "delay_reason_breakdown"),
])
def test_wants_different_grouping_true_negative(query, resolved_intent):
    assert schema_scope.wants_different_grouping(query, resolved_intent) is False


def test_chat_activity_summary_aggregate_view_still_scopes_normally():
    # The fix must not collateral-damage the legitimate aggregate view —
    # it's a different schema key (v_chat_activity_summary) from the raw
    # table (shipment_chat_log) being excluded.
    scoped = schema_scope.scope_schema("how is the AI chat feature being used")
    assert "v_chat_activity_summary" in scoped.entities
