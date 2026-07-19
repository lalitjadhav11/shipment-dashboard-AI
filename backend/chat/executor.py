"""
Stage 6 — Execute (no LLM). Runs validated SQL, with bound parameters, over
the read-only agent_ro connection. By the time execution reaches here, the
query has already passed Stage 5, so this stage only has to worry about
runtime failures (bad tracking_id, timeout), not shape/safety.
"""
import time
from dataclasses import dataclass

from .db import get_agent_cursor, clean_rows


class ExecutionError(Exception):
    """A query that already passed Stage 5 (so it's structurally safe) still
    failed at runtime — timeout, deadlock, whatever. This must degrade to a
    clean decline in pipeline.py, not crash the SSE stream, the same defense
    already applied to audit.py's FK-violation case. Real, demonstrated
    trigger: a legitimately expensive query (the original daily_volume_trend
    template, before it was rewritten — see AGENTIC_RAG_ARCHITECTURE.md §9)
    exceeding the agent_ro role's statement_timeout and raising
    psycopg2.errors.QueryCanceled uncaught."""


@dataclass
class ExecutionResult:
    rows: list
    row_count: int
    elapsed_ms: float


def execute_query(sql: str, params: dict) -> ExecutionResult:
    start = time.monotonic()
    try:
        with get_agent_cursor() as cur:
            cur.execute(sql, params)
            raw_rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — any DB runtime failure
        raise ExecutionError(str(exc)) from exc
    elapsed_ms = (time.monotonic() - start) * 1000

    rows = clean_rows(raw_rows)
    return ExecutionResult(rows=rows, row_count=len(rows), elapsed_ms=round(elapsed_ms, 1))
