"""
Router-level security fixes, found via live reproduction while auditing
Phases 1-4:
  1. ChatRequest had no length limits — an oversized session_id (VARCHAR(64)
     column) crashed the entire SSE stream mid-response with an uncaught
     psycopg2.errors.StringDataRightTruncation; an unbounded query is a real
     cost-abuse vector (embedded + sent to a paid LLM verbatim).
  2. GET /api/chat/history and GET /api/chat/llm-usage had no access control
     at all — /history returns raw user_query/ai_response content (plus a
     raw customer_id UUID) for any tracking_id an unauthenticated caller
     supplies, and tracking_ids are guessable/enumerable, not secrets.
Tested directly against the Pydantic model / gate function — no HTTP client
needed, consistent with the rest of this offline suite.
"""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from chat import router


def test_session_id_over_max_length_rejected():
    with pytest.raises(ValidationError):
        router.ChatRequest(query="hello", session_id="x" * (router.SESSION_ID_MAX_LEN + 1))


def test_session_id_at_max_length_accepted():
    req = router.ChatRequest(query="hello", session_id="x" * router.SESSION_ID_MAX_LEN)
    assert len(req.session_id) == router.SESSION_ID_MAX_LEN


def test_session_id_omitted_is_fine():
    req = router.ChatRequest(query="hello")
    assert req.session_id is None


def test_query_over_max_length_rejected():
    with pytest.raises(ValidationError):
        router.ChatRequest(query="x" * (router.QUERY_MAX_LEN + 1))


def test_empty_query_rejected():
    with pytest.raises(ValidationError):
        router.ChatRequest(query="")


def test_normal_query_accepted():
    req = router.ChatRequest(query="Where is tracking number 700000000001?")
    assert req.query.startswith("Where")


@pytest.mark.parametrize("role", ["SUPPORT", "OPS", "ADMIN", "support", "ops", "admin"])
def test_verbose_role_gate_allows_privileged_roles(role):
    router._require_verbose_role(role)  # must not raise


@pytest.mark.parametrize("role", ["CUSTOMER", "customer", "", "GUEST", "ADMINISTRATOR"])
def test_verbose_role_gate_blocks_everyone_else(role):
    with pytest.raises(HTTPException) as exc_info:
        router._require_verbose_role(role)
    assert exc_info.value.status_code == 403
