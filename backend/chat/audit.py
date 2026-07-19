"""
Writes every chat interaction to shipment_chat_log — the system's sole audit
trail in Phase 1 (see AGENTIC_RAG_ARCHITECTURE.md §4 Stage 7 and §8
Governance). This is the ONE deliberate write path in the whole chat
pipeline, and it's a single hardcoded INSERT, never agent/LLM-generated SQL
— so it uses the regular app DB role (DATABASE_URL, same as main.py), not
the read-only agent_ro role that Stages 4-6 are restricted to.
"""
import os

import psycopg2
from psycopg2.extras import Json

APP_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@db:5432/shipdb_phase1",
)

_INSERT_SQL = """
    INSERT INTO shipment_chat_log
        (tracking_id, customer_id, user_query, ai_response,
         context_snapshot, confidence_score)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


def log_chat_interaction(
    *,
    tracking_id: str | None,
    customer_id: str | None,
    user_query: str,
    ai_response: str,
    context_snapshot: dict,
    confidence_score: float | None,
) -> None:
    # tracking_id here is the raw value Stage 2 extracted from free text —
    # it was NEVER confirmed to exist in `shipments` (that's exactly what a
    # "not found" answer means: the extracted ID doesn't match any real
    # shipment). Both columns are real FKs (ON DELETE SET NULL), so a
    # typo'd/nonexistent ID raises ForeignKeyViolation on insert. Audit
    # logging is a side effect, not the primary response — it must never
    # crash the request the user is waiting on, so on that specific error we
    # retry once with the unverified reference(s) nulled out rather than
    # losing the audit row (or the answer) entirely.
    conn = psycopg2.connect(APP_DATABASE_URL)
    try:
        params = (tracking_id, customer_id, user_query, ai_response,
                  Json(context_snapshot), confidence_score)
        try:
            with conn.cursor() as cur:
                cur.execute(_INSERT_SQL, params)
            conn.commit()
        except psycopg2.errors.ForeignKeyViolation:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(_INSERT_SQL, (None, None, *params[2:]))
            conn.commit()
    finally:
        conn.close()
