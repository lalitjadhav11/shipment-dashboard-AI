"""
Stage 5 guardrail regression tests — pure sqlglot parsing, no DB/LLM needed.

The most valuable check here is the blanket one: every template in
sql_templates.TEMPLATES must independently pass its own guardrail check. This
is exactly the class of bug found live twice (AGENTIC_RAG_ARCHITECTURE.md
§9's aliased COUNT(*), §10's nested-subquery alias in daily_volume_trend) —
both were template SQL that Stage 5 wrongly rejected, discovered only by
manually exercising that one template. Looping over every template catches a
guardrail regression on ANY of them the moment a template or the validator
changes, instead of waiting for someone to happen to ask that exact question.
"""
import pytest

from chat import guardrails
from chat.sql_templates import TEMPLATES


@pytest.mark.parametrize("intent", sorted(TEMPLATES.keys()))
def test_every_template_passes_its_own_guardrail(intent):
    spec = TEMPLATES[intent]
    # Should not raise — entity_keys is each template's own declared allow-list.
    guardrails.validate_sql(spec.sql, list(spec.entity_keys))


def test_rejects_write_operations():
    for bad_sql in [
        "INSERT INTO shipments (tracking_id) VALUES ('x')",
        "UPDATE shipments SET current_status = 'LOST'",
        "DELETE FROM shipments",
        "DROP TABLE shipments",
    ]:
        with pytest.raises(guardrails.GuardrailError):
            guardrails.validate_sql(bad_sql, ["shipment"])


def test_rejects_tables_outside_scope():
    # customers is a real table, but not in the allow-list passed here.
    with pytest.raises(guardrails.GuardrailError):
        guardrails.validate_sql("SELECT * FROM customers", ["shipment"])


def test_rejects_multiple_statements():
    with pytest.raises(guardrails.GuardrailError):
        guardrails.validate_sql(
            "SELECT * FROM shipments; SELECT * FROM customers",
            ["shipment", "customer"],
        )


def test_accepts_nested_subquery_alias():
    # Regression for AGENTIC_RAG_ARCHITECTURE.md §10 bug 2 — `cnt` is a
    # derived table's own alias, only visible by walking every nested SELECT.
    sql = """
        SELECT COALESCE(c.day, d.day) AS day, COALESCE(c.cnt, 0) AS shipments_created
        FROM (SELECT created_at::date AS day, count(*) AS cnt FROM shipments GROUP BY created_at::date) c
        FULL OUTER JOIN (SELECT delivery_date::date AS day, count(*) AS cnt FROM shipments GROUP BY delivery_date::date) d
        ON d.day = c.day
    """
    guardrails.validate_sql(sql, ["shipment"])


def test_auto_appends_limit_when_missing():
    result = guardrails.validate_sql("SELECT tracking_id FROM shipments", ["shipment"])
    assert "LIMIT" in result.upper()


def test_unknown_column_rejected():
    with pytest.raises(guardrails.GuardrailError):
        guardrails.validate_sql("SELECT ssn FROM shipments", ["shipment"])


def test_forbidden_table_rejected_even_when_explicitly_allow_listed():
    # Independent backstop for schema_scope.py's NEVER_SCOPE_ENTITIES — this
    # must reject shipment_chat_log even if the caller's allow-list
    # (normally derived from Stage 3, which should never produce this in
    # the first place — see test_schema_scope.py) explicitly includes it.
    # Two layers agreeing "never" is the point, not one check duplicated.
    with pytest.raises(guardrails.GuardrailError):
        guardrails.validate_sql(
            "SELECT user_query, ai_response FROM shipment_chat_log",
            ["shipment_chat_log"],
        )
