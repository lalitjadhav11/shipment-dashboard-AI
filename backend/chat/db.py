"""
Read-only DB access for the chat agent, via the dedicated agent_ro Postgres
role (see db/init/03_agent_ro_role.sh). Kept separate from main.py's
DATABASE_URL connection, which uses the full-privilege app user.

Every connection here is: read-only at the Postgres level (agent_ro has no
write grants), read-only at the transaction level (defense in depth — belt
and braces per AGENTIC_RAG_ARCHITECTURE.md §4 Stage 5), time-boxed, and
always rolled back.
"""
import os
from contextlib import contextmanager
from decimal import Decimal
from datetime import datetime, date, time

import psycopg2
from psycopg2.extras import RealDictCursor

AGENT_DATABASE_URL = os.environ.get(
    "AGENT_DATABASE_URL",
    "postgresql://agent_ro:agent_ro_pw@db:5432/shipdb_phase1",
)

STATEMENT_TIMEOUT_MS = int(os.environ.get("AGENT_STATEMENT_TIMEOUT_MS", "3000"))


@contextmanager
def get_agent_cursor():
    conn = psycopg2.connect(AGENT_DATABASE_URL)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = %s;", (STATEMENT_TIMEOUT_MS,))
            cur.execute("SET TRANSACTION READ ONLY;")
            yield cur
    finally:
        # Always roll back — this connection should never persist a write,
        # and agent_ro can't write anyway, but this closes the loop.
        conn.rollback()
        conn.close()


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def clean_rows(rows) -> list:
    """Recursively converts Decimal/datetime values (including inside JSONB
    columns already deserialized to dict/list by psycopg2) into JSON-safe
    Python primitives — mirrors main.py's _clean() for the app-role queries."""
    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        return _jsonable(obj)

    return [_clean(dict(row)) for row in rows]
