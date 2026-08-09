"""
Stage 4b unit tests — mocks llm_client.call_tool so these run offline (no
real LLM call, no cost, deterministic), same principle as the rest of this
suite: verify the deterministic code AROUND the LLM call, not the LLM itself.
"""
from chat import sql_llm, entities


def _fake_entities(**enum_matches):
    e = entities.ExtractedEntities()
    e.enum_matches = enum_matches
    return e


def test_draft_sql_narrows_params_to_only_what_sql_references(monkeypatch):
    # Regression for a live bug found while verifying the grouping-dimension
    # fix: "group shipments by package type and show how many are delayed"
    # spuriously enum-matches current_status=PACKAGE_RECEIVED (the "package"
    # prefix-word noise documented in entities.py/§10), and the LLM correctly
    # drafts SQL that does NOT filter on it — but the old code still passed
    # the full available_params superset downstream, causing Stage 7 to
    # falsely claim that value had been applied as a filter. FilledTemplate
    # .params must contain ONLY placeholders the drafted SQL actually uses.
    monkeypatch.setattr(
        sql_llm.llm_client, "call_tool",
        lambda **kwargs: {
            "sql": "SELECT package_type, COUNT(*) FROM shipments GROUP BY package_type",
            "explanation": "groups by package type",
        },
    )
    extracted = _fake_entities(current_status="PACKAGE_RECEIVED")
    filled = sql_llm.draft_sql(
        "group shipments by package type and show how many are delayed",
        ["shipment"], extracted,
    )
    assert filled is not None
    assert filled.params == {}  # current_status was extracted but never referenced in the SQL


def test_draft_sql_keeps_params_the_sql_actually_uses(monkeypatch):
    monkeypatch.setattr(
        sql_llm.llm_client, "call_tool",
        lambda **kwargs: {
            "sql": "SELECT * FROM shipments WHERE current_status = %(current_status)s",
            "explanation": "filters by status",
        },
    )
    extracted = _fake_entities(current_status="CUSTOMS_HOLD")
    filled = sql_llm.draft_sql("shipments held in customs", ["shipment"], extracted)
    assert filled is not None
    assert filled.params == {"current_status": "CUSTOMS_HOLD"}


def test_draft_sql_retry_context_reaches_the_prompt(monkeypatch):
    captured = {}

    def fake_call_tool(*, system_prompt, **kwargs):
        captured["prompt"] = system_prompt
        return {"sql": "SELECT tracking_id FROM shipments", "explanation": "fixed"}

    monkeypatch.setattr(sql_llm.llm_client, "call_tool", fake_call_tool)
    sql_llm.draft_sql(
        "some query", ["shipment"], _fake_entities(),
        previous_sql="SELECT * FROM shipment_chat_log",
        retry_context="query references forbidden tables: {'shipment_chat_log'}",
    )
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" in captured["prompt"]
    assert "forbidden tables" in captured["prompt"]
    assert "SELECT * FROM shipment_chat_log" in captured["prompt"]


def test_draft_sql_first_attempt_prompt_has_no_retry_section(monkeypatch):
    captured = {}

    def fake_call_tool(*, system_prompt, **kwargs):
        captured["prompt"] = system_prompt
        return {"sql": "SELECT tracking_id FROM shipments", "explanation": "ok"}

    monkeypatch.setattr(sql_llm.llm_client, "call_tool", fake_call_tool)
    sql_llm.draft_sql("some query", ["shipment"], _fake_entities())
    assert "YOUR PREVIOUS ATTEMPT" not in captured["prompt"]


def test_draft_sql_forwards_model_override_to_call_tool(monkeypatch):
    captured = {}

    def fake_call_tool(*, model_override, **kwargs):
        captured["model_override"] = model_override
        return {"sql": "SELECT tracking_id FROM shipments", "explanation": "ok"}

    monkeypatch.setattr(sql_llm.llm_client, "call_tool", fake_call_tool)
    sql_llm.draft_sql("some query", ["shipment"], _fake_entities(), model_override="claude-sonnet-5")
    assert captured["model_override"] == "claude-sonnet-5"


def test_draft_sql_rejects_sql_referencing_unavailable_param(monkeypatch):
    monkeypatch.setattr(
        sql_llm.llm_client, "call_tool",
        lambda **kwargs: {
            "sql": "SELECT * FROM shipments WHERE tracking_id = %(tracking_id)s",
            "explanation": "looks up by tracking id",
        },
    )
    extracted = _fake_entities()  # no tracking_id extracted
    filled = sql_llm.draft_sql("some query", ["shipment"], extracted)
    assert filled is None
