"""
FastAPI routes for the Shipment Journey Summary chat (v0 — see
AGENTIC_RAG_ARCHITECTURE.md). POST /api/chat streams the pipeline's trace as
Server-Sent Events; GET /api/chat/history is a fixed, hardcoded read for QA
review of shipment_chat_log (not part of the agent's SQL-generation path, so
it doesn't need Stage 5's validator — its query shape is code, not agent
output).
"""
from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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


class ChatRequest(BaseModel):
    query: str


def _stream(query: str, verbose: bool):
    for event in pipeline.run_pipeline(query):
        if verbose or event["stage"] == "answer_ready":
            yield sse_event(event["stage"], event["detail"])


@router.post("")
def chat(body: ChatRequest, x_user_role: str = Header(default="CUSTOMER", alias="X-User-Role")):
    verbose = x_user_role.upper() in VERBOSE_ROLES
    return StreamingResponse(_stream(body.query, verbose), media_type="text/event-stream")


@router.get("/history")
def chat_history(tracking_id: str | None = None, limit: int = 20):
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
