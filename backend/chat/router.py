"""
FastAPI routes for the Shipment Journey Summary chat (v0 — see
AGENTIC_RAG_ARCHITECTURE.md). POST /api/chat streams the pipeline's trace as
Server-Sent Events; GET /api/chat/history is a fixed, hardcoded read for QA
review of shipment_chat_log (not part of the agent's SQL-generation path, so
it doesn't need Stage 5's validator — its query shape is code, not agent
output).
"""
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import pipeline
from .db import get_agent_cursor, clean_rows
from .trace import sse_event

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Verbose "thinking" trace is a privilege (AGENTIC_RAG_ARCHITECTURE.md §5),
# not a client-controlled flag. There's no real auth/session system in
# Phase 1 yet, so X-User-Role is a PLACEHOLDER for that check — replace this
# with a real role lookup off the authenticated session before this ships
# past a demo.
VERBOSE_ROLES = {"SUPPORT", "OPS", "ADMIN"}
HISTORY_LIMIT_MAX = 100

# session_id is stored in a VARCHAR(64) column — capping it here means a
# malformed/oversized value gets a clean 422 at the API boundary instead of
# reaching audit.py's INSERT and raising psycopg2.errors.StringDataRightTruncation
# uncaught mid-SSE-stream (a live-reproduced bug: crashes the whole response,
# the exact failure shape the FK-violation retry in audit.py already guards
# against for tracking_id/customer_id — this path just never had the same
# protection). audit.py still catches it defensively too, in case a future
# caller ever constructs the pipeline directly and skips this validation.
SESSION_ID_MAX_LEN = 64
# No enforced limit existed on the query text at all — an arbitrarily long
# query is a real cost-abuse vector (it's embedded, and for Stage 4b/7v1
# gets sent to a paid LLM verbatim) and, incidentally, uncaps how many
# tracking_ids a single query could pack in for the comparison path. 4000
# chars is generous for a genuine question with room to spare.
QUERY_MAX_LEN = 4000


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=QUERY_MAX_LEN)
    # Phase 3 session memory (chat/session.py) — a client-generated opaque
    # string (e.g. crypto.randomUUID() in the frontend, kept for one
    # conversation), never validated/issued server-side since there's no
    # real session system yet. Entirely optional: omitting it just means no
    # follow-up context is available, same as before this existed.
    session_id: str | None = Field(default=None, max_length=SESSION_ID_MAX_LEN)


def _require_verbose_role(x_user_role: str) -> None:
    """Shared gate for the internal/QA-only endpoints below (/history,
    /llm-usage) — same placeholder role check as the verbose chat trace
    (see VERBOSE_ROLES' docstring above). Both endpoints used to have NO
    access control at all: /history returns raw user_query/ai_response
    content (plus a raw customer_id UUID) for any tracking_id an
    unauthenticated caller supplies, and tracking_ids are guessable/
    enumerable 9-15 digit numbers, not secrets — a real disclosure gap, not
    a theoretical one."""
    if x_user_role.upper() not in VERBOSE_ROLES:
        raise HTTPException(status_code=403, detail="requires a SUPPORT/OPS/ADMIN role")


def _stream(query: str, verbose: bool, session_id: str | None):
    for event in pipeline.run_pipeline(query, session_id=session_id):
        if verbose or event["stage"] == "answer_ready":
            yield sse_event(event["stage"], event["detail"])


@router.post("")
def chat(body: ChatRequest, x_user_role: str = Header(default="CUSTOMER", alias="X-User-Role")):
    verbose = x_user_role.upper() in VERBOSE_ROLES
    return StreamingResponse(_stream(body.query, verbose, body.session_id), media_type="text/event-stream")


@router.get("/history")
def chat_history(
    tracking_id: str | None = None, limit: int = 20,
    x_user_role: str = Header(default="CUSTOMER", alias="X-User-Role"),
):
    _require_verbose_role(x_user_role)
    sql = (
        "SELECT chat_id, tracking_id, customer_id, user_query, ai_response, "
        "confidence_score, created_at FROM shipment_chat_log"
    )
    params = {"limit": min(limit, HISTORY_LIMIT_MAX)}
    if tracking_id:
        sql += " WHERE tracking_id = %(tracking_id)s"
        params["tracking_id"] = tracking_id
    sql += " ORDER BY created_at DESC LIMIT %(limit)s"

    with get_agent_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return clean_rows(rows)


@router.get("/llm-usage")
def chat_llm_usage(x_user_role: str = Header(default="CUSTOMER", alias="X-User-Role")):
    """Template-vs-LLM traffic split, by provider, with latency and
    guardrail-rejection counts — the "minimize LLM load" design goal made
    queryable instead of only log-greppable. Backed by v_chat_llm_usage
    (db/init/02_dashboard_summary_views.sql), same read-only agent_ro role
    as /history above."""
    _require_verbose_role(x_user_role)
    with get_agent_cursor() as cur:
        cur.execute("SELECT * FROM v_chat_llm_usage;")
        rows = cur.fetchall()
    return clean_rows(rows)
