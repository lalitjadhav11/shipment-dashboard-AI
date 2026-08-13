"""
Phase B run report — writes evals/runs/<timestamp>.json (one record per
faithfulness case) and prints a summary table. No DB table yet — starting
with files matches this project's own "don't build infra before it's
needed" discipline (see the eval architecture plan's Phase C note: a
v_eval_runs-style view is a natural addition once there's a real reason to
query trends in SQL, not before).
"""
import json
import os
from datetime import datetime, timezone

RUNS_DIR = os.path.join(os.path.dirname(__file__), "runs")


class RunReport:
    def __init__(self):
        self.results = []

    def record(self, *, case_id, query, answer, deterministic_pass, score=None, reason=None, judge_model=None):
        self.results.append({
            "case_id": case_id,
            "query": query,
            "answer": answer,
            "deterministic_pass": deterministic_pass,
            "score": score,
            "reason": reason,
            "judge_model": judge_model,
        })

    def write(self) -> str:
        os.makedirs(RUNS_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(RUNS_DIR, f"{ts}.json")
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2)
        return path

    def print_summary(self):
        print("\n=== Phase B faithfulness eval summary ===")
        for r in self.results:
            score_str = f"{r['score']:.2f}" if r["score"] is not None else "n/a"
            status = "PASS" if r["deterministic_pass"] else "FAIL"
            print(f"[{status}] {r['case_id']:35s} score={score_str:>5s}  judge={r['judge_model']}")


# Module-level singleton — test_faithfulness.py's parametrized cases all
# record into the same report within one pytest session, written out once by
# a module-scoped fixture teardown.
_report = RunReport()


def get_report() -> RunReport:
    return _report
