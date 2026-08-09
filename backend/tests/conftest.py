"""
Shared fixtures for the chat pipeline regression suite.

Design goal: every test here runs offline — no live Postgres, no live LLM
call — so the full ~90-query golden set (see CHAT_TEST_QUERIES.md) runs in a
few seconds and can gate every change to backend/chat/, the same way the
project's own bug-fix history (AGENTIC_RAG_ARCHITECTURE.md) has repeatedly
needed but never had until now.

Two things make that possible without weakening what's tested:
  1. schema_loader's embedding indexes (Stage 1 intent bank, Stage 3 schema
     index) only need 02_phase1_agentic_schema.json + the sentence-transformers
     model — no DB. Built once per test session (matching how the real app's
     startup hook calls schema_loader.warm_up() once, not per-request).
  2. entities.py's org_name/city caches are the only thing that genuinely
     touches Postgres (a `SELECT org_name FROM customers` / distinct cities
     query). They already degrade to an empty list on any connection failure
     (see entities.py's `except Exception: return _ORG_NAME_CACHE["names"]`)
     — but silently returning "no match" would make every customer/location
     template untestable here. Patching the cache directly with fixture data
     (matching real values documented in CHAT_TEST_QUERIES.md) gets the same
     deterministic, DB-free coverage instead.
"""
import time

import pytest

from chat import schema_loader, entities

FIXTURE_ORG_NAMES = [
    "Smith Ltd", "Walker PLC", "Brown and Sons", "Acme Corp",
    "Daniel and Sons", "Baker LLC", "Globex Corporation",
]
FIXTURE_CITIES = [
    "Atlanta", "Washington", "Austin", "Chicago", "New York",
    "Kansas City", "Miami", "Seattle", "San Francisco", "Berlin",
]


@pytest.fixture(scope="session", autouse=True)
def _warm_schema_state():
    """Build the embedding indexes once for the whole test session — mirrors
    main.py's startup hook, and avoids reloading the sentence-transformers
    model (the slow part) per test."""
    schema_loader.warm_up()


@pytest.fixture(scope="session", autouse=True)
def _no_live_llm_calls():
    """Regression tests assert ROUTING decisions (which template/gate fires),
    not what a real LLM drafts — Stage 4b/7v1 output is non-deterministic and
    costs real money/quota (see AGENTIC_RAG_ARCHITECTURE.md's provider notes),
    so it's deliberately out of scope for this suite. Forcing the anthropic
    provider with no API key makes llm_client.call_tool() return None
    instantly (a plain env-var check, no network call) — so any test that
    exercises the full pipeline for a query that SHOULD fall through to
    Stage 4b still runs fast and deterministically; it just observes the
    graceful-degradation path (the clarifying answer) instead of live LLM
    output, which is exactly what "no provider configured" is supposed to do.
    """
    import os
    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AGENT_LLM_PROVIDER"):
        os.environ.pop(var, None)


@pytest.fixture(autouse=True)
def _patched_org_and_city_cache(monkeypatch):
    """Pre-fills entities.py's module-level caches with fixture data and a
    fresh timestamp, so _load_org_names()/_load_city_names() return it
    directly without ever attempting a DB connection (their TTL check short-
    circuits before the try/connect block runs)."""
    now = time.monotonic()
    monkeypatch.setitem(entities._ORG_NAME_CACHE, "names", list(FIXTURE_ORG_NAMES))
    monkeypatch.setitem(entities._ORG_NAME_CACHE, "loaded_at", now)
    monkeypatch.setitem(entities._LOCATION_CACHE, "names", list(FIXTURE_CITIES))
    monkeypatch.setitem(entities._LOCATION_CACHE, "loaded_at", now)


def consume_until(gen, *stop_stages):
    """Advances a pipeline.run_pipeline() generator and collects trace events
    up to and including the first one whose stage is in `stop_stages`, then
    stops — the generator is simply abandoned without calling next() again,
    so any DB/LLM-touching code after that point in pipeline.py's body never
    executes. This is what makes it safe to assert on ROUTING (e.g.
    "sql_generated" with the expected template/SQL) without needing a live
    DB for Stage 6 or a live LLM for Stage 4b/7."""
    events = []
    for event in gen:
        events.append(event)
        if event["stage"] in stop_stages:
            return events
    return events  # generator exhausted (e.g. hit answer_ready) before any stop_stage
