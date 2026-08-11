"""
Stage 2.5 — lightweight, DB-backed session memory (Phase 3).

No auth/session system exists yet (see router.py's X-User-Role placeholder
note) — session_id is just a client-generated opaque string the frontend
keeps for one conversation, threaded through so a narrow class of follow-up
question ("what about it", "and is that one delayed too") can resolve
without the user re-typing a tracking_id every turn.

Deliberately narrow on two axes, both for the same reason — a confidently
WRONG carried-forward entity is worse than no session memory at all, the
same lesson this project has hit repeatedly for single-turn queries
(AGENTIC_RAG_ARCHITECTURE.md §9, §11):
  1. Reuses shipment_chat_log (already the system's audit trail) as the
     backing store instead of introducing a new cache/session store — one
     source of truth, no new infrastructure for something Postgres already
     tracks.
  2. Only ever carries forward tracking_id, and only for queries that look
     like a genuine pronoun-style follow-up with no entity of their own —
     never for a query that already resolves fine on its own (a fresh
     fleet-wide question shouldn't silently inherit the last shipment just
     because it also happens to omit a tracking_id).
"""
import re

from .db import get_agent_cursor

FOLLOWUP_MAX_WORDS = 8

_FOLLOWUP_PRONOUN_RE = re.compile(r"\b(?:it|that|this)\b", re.IGNORECASE)
# A fleet-wide/plural noun anywhere in the query is treated as decisive
# evidence this is a genuinely new, unrelated question that merely happens
# to have no tracking_id of its own — not a follow-up about a specific
# shipment. Mirrors schema_scope.py's RECORD_LEVEL/aggregate distinction:
# same "singular subject vs. fleet-wide" signal, reused for a different
# purpose.
_FLEET_WIDE_HINT_RE = re.compile(
    r"\b(?:shipments?|orders?|packages?|customers?|fleet|overall|total)\b", re.IGNORECASE
)


def wants_session_context(query: str, extracted) -> bool:
    """True only for a short, entity-less query containing a referential
    pronoun and no fleet-wide language — see module docstring for why this
    stays narrow rather than trying to catch every possible follow-up
    phrasing. `extracted` is Stage 2's ExtractedEntities for this query."""
    if extracted.tracking_id or extracted.org_name or extracted.location or extracted.enum_matches:
        return False
    if len(query.split()) > FOLLOWUP_MAX_WORDS:
        return False
    if _FLEET_WIDE_HINT_RE.search(query):
        return False
    return bool(_FOLLOWUP_PRONOUN_RE.search(query))


def get_last_tracking_id(session_id: str) -> str | None:
    """Most recent tracking_id mentioned in this session, or None if there
    isn't one (a new session, or the last turn was itself fleet-wide/
    entity-less). A DB hiccup degrades to None (no context carried forward)
    rather than ever raising — the same 'auxiliary lookup, never load-
    bearing enough to crash the request' pattern as entities.py's
    org_name/city caches."""
    try:
        with get_agent_cursor() as cur:
            cur.execute(
                "SELECT tracking_id FROM shipment_chat_log "
                "WHERE session_id = %(session_id)s AND tracking_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1;",
                {"session_id": session_id},
            )
            row = cur.fetchone()
        return row["tracking_id"] if row else None
    except Exception:
        return None
