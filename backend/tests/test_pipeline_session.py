"""
Phase 3 session memory, integrated into the full pipeline. session.py's DB
lookup is mocked (no real Postgres needed) so this stays offline like the
rest of the suite.
"""
from chat import pipeline

from tests.conftest import consume_until


def test_followup_query_inherits_tracking_id_from_session(monkeypatch):
    monkeypatch.setattr(pipeline.session_stage, "get_last_tracking_id", lambda sid: "700000000001")

    events = consume_until(
        pipeline.run_pipeline("where is it now", session_id="session-abc"),
        "sql_generated",
    )
    stages = [e["stage"] for e in events]
    assert "sql_generated" in stages

    entities_event = [e for e in events if e["stage"] == "entities_extracted"][0]
    assert entities_event["detail"]["tracking_id"] == "700000000001"
    assert entities_event["detail"]["session_context_used"] is True

    sql_event = [e for e in events if e["stage"] == "sql_generated"][0]
    assert "v_shipment_journey_summary" in sql_event["detail"]["sql"]


def test_no_session_id_means_no_context_carried_forward(monkeypatch):
    # Same call as above but session_id omitted — must never even attempt
    # the lookup, let alone inherit anything.
    called = {"n": 0}

    def fake_lookup(sid):
        called["n"] += 1
        return "700000000001"

    monkeypatch.setattr(pipeline.session_stage, "get_last_tracking_id", fake_lookup)

    events = consume_until(pipeline.run_pipeline("where is it now"), "entities_extracted")
    entities_event = events[-1]
    assert entities_event["detail"]["tracking_id"] is None
    assert entities_event["detail"]["session_context_used"] is False
    assert called["n"] == 0


def test_fresh_session_with_nothing_to_inherit_proceeds_normally(monkeypatch):
    monkeypatch.setattr(pipeline.session_stage, "get_last_tracking_id", lambda sid: None)

    events = consume_until(
        pipeline.run_pipeline("what about it", session_id="brand-new-session"),
        "entities_extracted",
    )
    entities_event = events[-1]
    assert entities_event["detail"]["tracking_id"] is None
    assert entities_event["detail"]["session_context_used"] is False


def test_fleet_wide_followup_query_does_not_inherit(monkeypatch):
    # Guards the dangerous direction: a genuinely new, unrelated fleet-wide
    # question in the same session must NOT silently become scoped to the
    # previous turn's shipment.
    monkeypatch.setattr(pipeline.session_stage, "get_last_tracking_id", lambda sid: "700000000001")

    events = consume_until(
        pipeline.run_pipeline("what's our on-time percentage", session_id="session-abc"),
        "entities_extracted",
    )
    entities_event = events[-1]
    assert entities_event["detail"]["tracking_id"] is None
    assert entities_event["detail"]["session_context_used"] is False


def test_session_id_is_persisted_on_every_turn(monkeypatch):
    captured = {}
    monkeypatch.setattr(pipeline, "log_chat_interaction", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(pipeline.session_stage, "get_last_tracking_id", lambda sid: None)

    consume_until(pipeline.run_pipeline("800000000131", session_id="session-xyz"), "answer_ready")
    assert captured["session_id"] == "session-xyz"
