"""
Multi-shipment comparison (Phase 3) — closes the gap AGENTIC_RAG_ARCHITECTURE.md
§9 flagged as still-open ("true multi-shipment comparison... still not
handled"). executor.execute_query is mocked with canned rows (no real DB
needed — the SQL shape itself is already covered by test_guardrails.py's
blanket per-template check... except this isn't a TEMPLATES entry, so it's
verified directly here too), and synthesize.synthesize is mocked for the
causal branch (no real LLM call).
"""
from chat import pipeline
from chat.executor import ExecutionResult
from chat.respond_template import ShipmentAnswer

from tests.conftest import consume_until

TWO_SHIPMENT_ROWS = [
    {
        "tracking_id": "700000000001", "current_status": "IN_TRANSIT",
        "reason_for_delay": "NONE", "delay_comments": None,
        "estimated_delivery": "2026-08-15T00:00:00Z", "delivery_date": None,
        "open_issue_count": 0, "is_international": False, "customs_status": "NOT_REQUIRED",
    },
    {
        "tracking_id": "100000000002", "current_status": "CUSTOMS_HOLD",
        "reason_for_delay": "CUSTOMS", "delay_comments": "Missing HS code.",
        "estimated_delivery": "2026-08-10T00:00:00Z", "delivery_date": None,
        "open_issue_count": 1, "is_international": True, "customs_status": "HELD",
    },
]


def test_compare_sql_passes_its_own_guardrail_check():
    from chat import guardrails
    guardrails.validate_sql(pipeline._COMPARE_SQL, pipeline._COMPARE_ENTITY_KEYS)


def test_compare_shipments_answer_deterministic_formatting():
    answer = pipeline._compare_shipments_answer(TWO_SHIPMENT_ROWS, ["700000000001", "100000000002"])
    assert answer["confidence_score"] == 1.0
    assert "700000000001" in answer["answer"]
    assert "100000000002" in answer["answer"]
    assert "CUSTOMS" in answer["answer"]


def test_compare_shipments_answer_notes_missing_ids():
    answer = pipeline._compare_shipments_answer(
        [TWO_SHIPMENT_ROWS[0]], ["700000000001", "999999999999"],
    )
    assert answer["confidence_score"] < 1.0
    assert "999999999999" in answer["answer"]


def test_compare_shipments_answer_handles_none_found():
    answer = pipeline._compare_shipments_answer([], ["999999999999", "888888888888"])
    assert answer["confidence_score"] < 1.0
    assert "couldn't find" in answer["answer"].lower()


def test_plain_comparison_query_uses_deterministic_formatter_not_llm(monkeypatch):
    monkeypatch.setattr(
        pipeline.executor, "execute_query",
        lambda sql, params: ExecutionResult(rows=TWO_SHIPMENT_ROWS, row_count=2, elapsed_ms=5.0),
    )

    events = consume_until(
        pipeline.run_pipeline("Compare 700000000001 and 100000000002"),
        "answer_ready",
    )
    stages = [e["stage"] for e in events]
    assert "multiple_tracking_ids_detected" in stages
    assert "causal_comparison_needs_llm" not in stages  # no LLM cost for a plain comparison

    final = events[-1]["detail"]
    assert final["confidence_score"] == 1.0
    assert "700000000001" in final["answer"]
    assert "100000000002" in final["answer"]


def test_causal_comparison_query_routes_to_llm_synthesis(monkeypatch):
    monkeypatch.setattr(
        pipeline.executor, "execute_query",
        lambda sql, params: ExecutionResult(rows=TWO_SHIPMENT_ROWS, row_count=2, elapsed_ms=5.0),
    )
    captured = {}

    def fake_synthesize(query, rows, sql=None, params=None):
        captured["rows"] = rows
        return ShipmentAnswer(
            answer="100000000002 is more delayed because it's held in customs for a missing HS code.",
            confidence_score=0.95, supporting_data=rows,
        )

    monkeypatch.setattr(pipeline.synthesize, "synthesize", fake_synthesize)

    events = consume_until(
        pipeline.run_pipeline("which of 700000000001 and 100000000002 is more delayed and why"),
        "answer_ready",
    )
    stages = [e["stage"] for e in events]
    assert "causal_comparison_needs_llm" in stages
    assert len(captured["rows"]) == 2

    final = events[-1]["detail"]
    assert "customs" in final["answer"].lower()
    assert final["confidence_score"] == 0.95


def test_more_than_two_tracking_ids_still_compares_all():
    answer = pipeline._compare_shipments_answer(
        TWO_SHIPMENT_ROWS, ["700000000001", "100000000002", "300000000003"],
    )
    assert "300000000003" in answer["answer"]  # correctly listed as missing, not silently dropped
