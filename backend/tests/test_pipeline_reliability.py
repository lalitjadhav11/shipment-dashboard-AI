"""
Phase 2 reliability: the guardrail-rejection retry ladder in pipeline.py.
sql_llm.draft_sql is mocked (stateful — different result per call) so this
runs offline with no real LLM cost, and consume_until stops at
"sql_validated" — before Stage 6 execution, so no DB is needed either.
"""
from chat import pipeline
from chat.sql_templates import FilledTemplate

from conftest import consume_until


def test_guardrail_rejection_retries_once_with_feedback_then_succeeds(monkeypatch):
    attempts = []

    def fake_draft_sql(query, scoped_entities, extracted, *,
                        previous_sql=None, retry_context=None, model_override=None):
        attempts.append(retry_context)
        if retry_context is None:
            # First attempt: FORBIDDEN_TABLES rejects this regardless of
            # entity_keys (see guardrails.py's independent backstop) —
            # guaranteed guardrail rejection without depending on Stage 3's
            # real scoping for this query.
            return FilledTemplate(
                sql="SELECT * FROM shipment_chat_log", params={},
                entity_keys=["shipment"], source="llm",
            )
        return FilledTemplate(
            sql="SELECT tracking_id FROM shipments LIMIT 10", params={},
            entity_keys=["shipment"], source="llm",
        )

    monkeypatch.setattr(pipeline.sql_llm, "draft_sql", fake_draft_sql)

    events = consume_until(
        pipeline.run_pipeline("what's the weather today"),
        "sql_validated", "sql_rejected",
    )
    stages = [e["stage"] for e in events]

    assert "sql_rejected_retrying" in stages
    assert stages.count("sql_generated") == 2  # original attempt + the successful retry
    assert "sql_validated" in stages
    assert "sql_rejected" not in stages  # never gave up — the retry succeeded
    assert attempts == [None, "query references forbidden tables: {'shipment_chat_log'}"]

    retry_event = [e for e in events if e["stage"] == "sql_generated"][1]
    assert retry_event["detail"]["retry"] is True


def test_guardrail_rejection_declines_cleanly_when_retry_also_fails(monkeypatch):
    def always_forbidden(query, scoped_entities, extracted, *,
                          previous_sql=None, retry_context=None, model_override=None):
        return FilledTemplate(
            sql="SELECT * FROM shipment_chat_log", params={},
            entity_keys=["shipment"], source="llm",
        )

    monkeypatch.setattr(pipeline.sql_llm, "draft_sql", always_forbidden)

    events = consume_until(pipeline.run_pipeline("what's the weather today"), "sql_rejected")
    stages = [e["stage"] for e in events]
    assert "sql_rejected_retrying" in stages
    assert "sql_rejected" in stages  # gave up honestly after the retry also failed


def test_template_sourced_rejection_is_never_retried(monkeypatch):
    # A rejected TEMPLATE's SQL means a bug in hand-written SQL — retrying
    # via an LLM prompt can't fix that, so the retry ladder must not engage
    # for source="template" at all.
    def fake_fill_template(intent, entities):
        from chat.sql_templates import FilledTemplate
        return FilledTemplate(
            sql="SELECT * FROM shipment_chat_log", params={},
            entity_keys=["shipment"], source="template",
        )

    monkeypatch.setattr(pipeline.sql_templates, "fill_template", fake_fill_template)

    events = consume_until(
        pipeline.run_pipeline("Where is tracking number 700000000001 right now?"),
        "sql_rejected",
    )
    stages = [e["stage"] for e in events]
    assert "sql_rejected" in stages
    assert "sql_rejected_retrying" not in stages
