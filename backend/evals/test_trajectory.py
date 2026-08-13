"""
Phase A trajectory evals — data-driven growth of the same trace assertions
tests/test_intent_and_routing.py already makes by hand, parametrized over the
golden set in evals/golden/trajectory_cases.py. See that file's module
docstring for why this lives in evals/ rather than tests/.

@pytest.mark.eval only (not costly) — zero LLM cost, zero live DB, runs on
every commit alongside tests/.
"""
import pytest

from chat import pipeline

from conftest import consume_until
from evals.golden.trajectory_cases import CASES

pytestmark = pytest.mark.eval


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_trajectory(case, monkeypatch):
    if case.db_rows is not None:
        from chat.executor import ExecutionResult
        monkeypatch.setattr(
            pipeline.executor, "execute_query",
            lambda sql, params: ExecutionResult(rows=case.db_rows, row_count=len(case.db_rows), elapsed_ms=5.0),
        )

    events = consume_until(pipeline.run_pipeline(case.query), case.expect_stage)
    stages = [e["stage"] for e in events]

    assert case.expect_stage in stages, (
        f"[{case.id}] expected stage {case.expect_stage!r} to fire for query {case.query!r}, "
        f"got stages: {stages}\n{case.notes}"
    )

    for forbidden in case.must_not_fire:
        assert forbidden not in stages, (
            f"[{case.id}] stage {forbidden!r} must NOT fire for query {case.query!r}, "
            f"but it did — stages: {stages}\n{case.notes}"
        )

    if case.expect_forced_entities:
        scoped_events = [e for e in events if e["stage"] == "schema_scoped"]
        assert scoped_events, f"[{case.id}] expected a schema_scoped event but none fired"
        forced = scoped_events[0]["detail"]["forced_entities"]
        for entity in case.expect_forced_entities:
            assert entity in forced, (
                f"[{case.id}] expected {entity!r} to be force-scoped for query {case.query!r}, "
                f"got forced_entities: {forced}\n{case.notes}"
            )
