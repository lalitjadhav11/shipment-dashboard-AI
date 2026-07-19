"""
Stage 6 — Execute (no LLM). Runs validated SQL, with bound parameters, over
the read-only agent_ro connection. By the time execution reaches here, the
query has already passed Stage 5, so this stage only has to worry about
runtime failures (bad tracking_id, timeout), not shape/safety.
"""
import time
from dataclasses import dataclass

from .db import get_agent_cursor, clean_rows


@dataclass
class ExecutionResult:
    rows: list
    row_count: int
    elapsed_ms: float


def execute_query(sql: str, params: dict) -> ExecutionResult:
    start = time.monotonic()
    with get_agent_cursor() as cur:
        cur.execute(sql, params)
        raw_rows = cur.fetchall()
    elapsed_ms = (time.monotonic() - start) * 1000

    rows = clean_rows(raw_rows)
    return ExecutionResult(rows=rows, row_count=len(rows), elapsed_ms=round(elapsed_ms, 1))
