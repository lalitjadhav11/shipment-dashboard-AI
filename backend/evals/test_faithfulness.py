"""
Phase B faithfulness evals — LLM-judge scored, real cost, excluded from the
default `pytest` run (pytest.ini's `addopts = -m "not costly"`). Run
explicitly via `pytest -m costly`.

Calls synthesize.synthesize() DIRECTLY with each case's fixed row data —
bypasses Stages 1-6 entirely, isolating Stage 7 v1 (where these
hallucinations were actually found, see evals/golden/faithfulness_cases.py)
from SQL drafting/execution and from live data drift.

Two tiers of check per case, per the eval architecture plan:
  - must_include_facts / must_not_claim: deterministic, hard-gating — reliable
    enough to hard-fail CI on.
  - FaithfulnessMetric's continuous groundedness score: logged via report.py,
    NOT a hard gate yet — first runs establish a baseline before any
    threshold is set (same "don't guess a threshold" discipline as Phase 4's
    scoping_ms trigger, see AGENTIC_RAG_ARCHITECTURE.md §24).
"""
import json

import pytest
from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from chat import synthesize

from evals.golden.faithfulness_cases import CASES
from evals.judge import HouseJudgeModel
from evals.report import get_report

pytestmark = [pytest.mark.eval, pytest.mark.costly]


@pytest.fixture(scope="module")
def judge():
    return HouseJudgeModel()


@pytest.fixture(scope="module", autouse=True)
def _write_report_at_end():
    yield
    report = get_report()
    path = report.write()
    report.print_summary()
    print(f"\nFull report: {path}")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_faithfulness(case, judge):
    answer = synthesize.synthesize(case.query, case.row_data_fixture, sql=case.sql, params=case.params)
    report = get_report()

    missing = [fact for fact in case.must_include_facts if fact not in answer.answer]
    hallucinated = [phrase for phrase in case.must_not_claim if phrase.lower() in answer.answer.lower()]
    deterministic_pass = not missing and not hallucinated

    score, reason = None, None
    try:
        test_case = LLMTestCase(
            input=case.query,
            actual_output=answer.answer,
            retrieval_context=[json.dumps(row, default=str) for row in case.row_data_fixture],
        )
        metric = FaithfulnessMetric(model=judge, include_reason=True)
        score = metric.measure(test_case)
        reason = metric.reason
    except Exception as exc:  # noqa: BLE001 — a judge-side failure must not hide the deterministic result
        reason = f"FaithfulnessMetric failed: {exc}"

    report.record(
        case_id=case.id, query=case.query, answer=answer.answer,
        deterministic_pass=deterministic_pass, score=score, reason=reason,
        judge_model=judge.get_model_name(),
    )

    assert not missing, (
        f"[{case.id}] answer is missing expected fact(s) {missing}: {answer.answer!r}\n{case.notes}"
    )
    assert not hallucinated, (
        f"[{case.id}] answer contains a known hallucination pattern {hallucinated}: {answer.answer!r}\n{case.notes}"
    )
