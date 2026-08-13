"""
Phase B faithfulness-eval golden set — each case is a regression guard for a
hallucination class actually found live against Stage 7 v1 (synthesize.py),
documented in AGENTIC_RAG_ARCHITECTURE.md. Row data is a fixed fixture (not a
live DB query) so these test synthesize() in isolation, decoupled from data
drift and from Stages 1-6 entirely — see synthesize.synthesize()'s signature.

Field names in row_data_fixture deliberately match the real columns
Stage 6 would return (shipment_issues.reported_at/description,
v_shipment_journey_summary.journey_timeline's stage/location/event_timestamp
shape from db/init/01_phase1_schema.sql) — realistic fixtures, not
schema-agnostic placeholders, since a wrong field name synthesize() has never
actually seen wouldn't exercise the same code path a real query hits.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_NOW = datetime.now(timezone.utc)


@dataclass
class FaithfulnessCase:
    id: str
    query: str
    row_data_fixture: list
    must_include_facts: list = field(default_factory=list)   # substrings the answer MUST contain
    must_not_claim: list = field(default_factory=list)        # substrings the answer MUST NOT contain
    sql: str | None = None      # optional — for cases regression-testing _filter_context()
    params: dict | None = None
    notes: str = ""


CASES = [
    FaithfulnessCase(
        id="open_issue_age_no_double_offset",
        query="Which shipment issues have been open for more than a week?",
        row_data_fixture=[
            {
                "tracking_id": "700000000001", "issue_type": "CUSTOMS_HOLD",
                "description": "Missing HS code on declaration; awaiting broker resubmission.",
                "status": "OPEN",
                "reported_at": (_NOW - timedelta(days=30)).isoformat(),
            },
            {
                "tracking_id": "100000000002", "issue_type": "WEATHER_DELAY",
                "description": "Severe storm rerouting.",
                "status": "OPEN",
                "reported_at": (_NOW - timedelta(days=2)).isoformat(),
            },
        ],
        must_include_facts=["700000000001"],
        must_not_claim=[
            "no reference date", "no current date", "cannot calculate", "cannot determine",
            "none of the issues", "none have been open",
        ],
        notes="§14 Bug 3 — synthesize.py previously had no CURRENT DATE/TIME grounding and "
              "either claimed none qualified or declined outright, contradicting its own row "
              "data. Only the 30-day-old issue should be reported as open more than a week.",
    ),
    FaithfulnessCase(
        id="customer_name_filter_context",
        query="Show tracking IDs for customer Daniel and Sons",
        row_data_fixture=[
            {"tracking_id": "500000000010"},
            {"tracking_id": "500000000021"},
        ],
        sql="SELECT s.tracking_id FROM shipments s JOIN customers c "
            "ON c.customer_id = s.customer_id WHERE c.org_name = %(org_name)s",
        params={"org_name": "Daniel and Sons"},
        must_include_facts=["500000000010", "500000000021"],
        must_not_claim=[
            "don't have customer name", "no customer name information",
            "customer name isn't available", "doesn't include the customer",
        ],
        notes="synthesize.py's _filter_context() docstring — a WHERE-filtered query whose SELECT "
              "list doesn't repeat the filter column must not be read as 'no customer info exists'.",
    ),
    FaithfulnessCase(
        id="history_timeline_grounding",
        query="Give me details about 400000000014 and their history of status.",
        row_data_fixture=[
            {
                "tracking_id": "400000000014",
                "journey_timeline": [
                    {"stage": "PICKED_UP", "location": "Chicago", "event_timestamp": "2026-07-01T08:00:00Z", "notes": None},
                    {"stage": "IN_TRANSIT", "location": "Denver", "event_timestamp": "2026-07-02T14:00:00Z", "notes": None},
                    {"stage": "CUSTOMS_HOLD", "location": "Denver", "event_timestamp": "2026-07-03T09:00:00Z", "notes": "Missing HS code"},
                    {"stage": "IN_TRANSIT", "location": "Salt Lake City", "event_timestamp": "2026-07-05T11:00:00Z", "notes": None},
                    {"stage": "OUT_FOR_DELIVERY", "location": "Seattle", "event_timestamp": "2026-07-06T07:00:00Z", "notes": None},
                    {"stage": "DELIVERED", "location": "Seattle", "event_timestamp": "2026-07-06T15:00:00Z", "notes": None},
                ],
            },
        ],
        must_include_facts=["PICKED_UP", "DELIVERED", "Seattle"],
        must_not_claim=[
            "historical status timeline is not present", "no historical data",
            "doesn't have a history", "history is not available",
            "isn't present in the shipment record", "not present in the shipment record",
        ],
        notes="§20 — the LLM previously asserted a complete historical timeline 'wasn't present' "
              "for a shipment with 9 real logged stages, despite the data being right there in "
              "journey_timeline when it was actually queried. Guards Stage 7v1's read of a "
              "journey_timeline it's GIVEN (isolates synthesize(), independent of whether Stage "
              "4b's SQL draft chooses to query it — that's a separate, already-fixed concern).",
    ),
]
