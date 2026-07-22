"""
Phase 1 Shipment Dashboard — Backend API (boilerplate).

A deliberately small FastAPI app that proves the full stack is wired together:
  GET /                 -> hello world
  GET /health           -> liveness + DB connectivity
  GET /api/summary      -> the v_dashboard_headline snapshot (10 KPIs, one query)
  GET /api/status-breakdown -> v_status_breakdown

Extend from here for the real dashboard + AI chat endpoints.
"""
import json
import os
from contextlib import contextmanager
from decimal import Decimal
from datetime import datetime, date, time

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from chat.router import router as chat_router
from chat import schema_loader
from chat import llm_client

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@db:5432/shipdb_phase1",
)

app = FastAPI(title="Shipment Dashboard API — Phase 1", version="0.1.0")

# Allow the React dev/prod frontend to call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)


@app.on_event("startup")
def _warm_up_chat_agent() -> None:
    # Loads the embedding model + precomputes the intent/schema indexes once
    # at startup, so the first /api/chat request isn't the one paying for it.
    schema_loader.warm_up()


@contextmanager
def get_cursor():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return _jsonable(obj)


@app.get("/")
def hello():
    return {"message": "Hello World from the Shipment Dashboard backend 🚚"}


@app.get("/api/hello")
def api_hello():
    """Same hello, reachable through the frontend's /api proxy."""
    return {"message": "Hello World from the Shipment Dashboard backend 🚚"}


@app.get("/health")
def health():
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS ok;")
            cur.fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}")


@app.get("/api/summary")
def summary():
    """One-row headline snapshot for the top of the dashboard."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM v_dashboard_headline;")
        row = cur.fetchone()
    return _clean(row) if row else {}


@app.get("/api/status-breakdown")
def status_breakdown():
    with get_cursor() as cur:
        cur.execute("SELECT * FROM v_status_breakdown;")
        rows = cur.fetchall()
    return _clean(rows)


@app.get("/api/shipments/{tracking_id}")
def shipment_detail(tracking_id: str):
    """Single-shipment summary for the tracking-ID detail panel — same grounded
    context (status, customs, delay, full journey timeline) built for the AI chat."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM v_shipment_journey_summary WHERE tracking_id = %(tracking_id)s;",
            {"tracking_id": tracking_id},
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"shipment {tracking_id} not found")
    return _clean(row)


_SUMMARY_TOOL_NAME = "shipment_detailed_summary"
_SUMMARY_TOOL_SCHEMA = {
    "description": (
        "Write a detailed, customer-facing summary of this shipment, grounded ONLY "
        "in the JSON data provided — never invent a fact not present in it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "3-5 sentences covering: customer and order ID, current status, "
                    "origin -> destination and service type, customs status if "
                    "international, delay reason and explanation if any, estimated "
                    "delivery date (and actual delivery date/on-time-or-late if "
                    "delivered), and one line noting notable journey milestones "
                    "(e.g. hub stops, customs hold) drawn from the timeline."
                ),
            },
        },
        "required": ["summary"],
    },
}


@app.get("/api/shipments/{tracking_id}/ai-summary")
def shipment_ai_summary(tracking_id: str):
    """LLM-written detailed summary for the drawer's AI panel — reuses the
    exact same grounded data as /api/shipments/{tracking_id} (no separate DB
    round-trip through the chat pipeline's intent classifier, which tends to
    match a narrow single-field template for an open-ended "summarize this"
    question). The LLM only narrates; it never chooses what data to fetch."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM v_shipment_journey_summary WHERE tracking_id = %(tracking_id)s;",
            {"tracking_id": tracking_id},
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"shipment {tracking_id} not found")

    data = _clean(row)
    data.pop("customer_id", None)  # internal DB identifier — org_name is the customer-facing name
    result = llm_client.call_tool(
        system_prompt=(
            "You are a shipment tracking assistant. Write a detailed, clear, "
            "professional summary of the shipment described by the JSON data "
            "you're given. Ground every statement in that data — never invent "
            "or assume anything it doesn't contain. Never mention internal "
            "database identifiers (UUIDs, primary keys) — refer to the "
            "customer only by their organization name."
        ),
        user_message=json.dumps(data, default=str),
        tool_name=_SUMMARY_TOOL_NAME,
        tool_schema=_SUMMARY_TOOL_SCHEMA,
    )
    if not result or not result.get("summary"):
        raise HTTPException(status_code=503, detail="AI summary is unavailable right now")

    return {"tracking_id": tracking_id, "summary": result["summary"]}


class ShipmentAskRequest(BaseModel):
    query: str


_ASK_TOOL_NAME = "answer_shipment_question"
_ASK_TOOL_SCHEMA = {
    "description": (
        "Answer the user's question about this shipment, grounded ONLY in the "
        "primary shipment and related-shipments data provided — never invent "
        "a fact not present in it. If the data provided can't answer the "
        "question, say so plainly rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": (
                    "Concise, direct answer to the user's question — 1-2 "
                    "sentences, no preamble or restating the question. Lead "
                    "with the actual answer (a number, a status, a yes/no), "
                    "then at most one short supporting clause. Use the "
                    "related_shipments data when the question is about other, "
                    "similar, or comparable shipments — don't just describe the "
                    "primary shipment again."
                ),
            },
            "follow_up_suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "0-3 short, natural follow-up questions the user might ask next.",
            },
        },
        "required": ["answer"],
    },
}


@app.post("/api/shipments/{tracking_id}/ask")
def shipment_ask(tracking_id: str, body: ShipmentAskRequest):
    """Follow-up Q&A for the drawer's AI panel. Unlike /api/chat (which routes
    through intent classification against 28 fixed templates — great for
    fleet-wide dashboard questions, but prone to matching an overly narrow
    template for a compound "how does this compare to others" question),
    this endpoint always hands the LLM two things directly: the full primary
    shipment record, and a sample of shipments that share this one's customer
    or exact route. The LLM decides which set actually answers the question —
    no classifier guessing which single template applies."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM v_shipment_journey_summary WHERE tracking_id = %(tracking_id)s;",
            {"tracking_id": tracking_id},
        )
        primary = cur.fetchone()
        if not primary:
            raise HTTPException(status_code=404, detail=f"shipment {tracking_id} not found")

        cur.execute(
            """
            SELECT s.tracking_id, c.org_name, s.current_status, s.delivery_type,
                   s.is_international, s.customs_status, s.reason_for_delay,
                   s.estimated_delivery, s.delivery_date, s.src_loc, s.dest_loc
            FROM shipments s
            JOIN customers c ON c.customer_id = s.customer_id
            WHERE s.tracking_id != %(tracking_id)s
              AND (
                s.customer_id = %(customer_id)s
                OR (s.src_loc->>'city' = %(src_city)s AND s.dest_loc->>'city' = %(dest_city)s)
              )
            ORDER BY s.updated_at DESC
            LIMIT 25;
            """,
            {
                "tracking_id": tracking_id,
                "customer_id": primary["customer_id"],
                "src_city": (primary["src_loc"] or {}).get("city"),
                "dest_city": (primary["dest_loc"] or {}).get("city"),
            },
        )
        related = cur.fetchall()

    primary_data = _clean(primary)
    primary_data.pop("customer_id", None)
    related_data = _clean(related)

    result = llm_client.call_tool(
        system_prompt=(
            "You are a shipment tracking assistant answering a follow-up "
            "question. You're given the primary shipment's full data and a "
            "sample of related shipments (same customer or same route) for "
            "comparison. Ground your answer only in this data — never invent "
            "a fact, and never mention internal database identifiers (UUIDs, "
            "primary keys). Be concise: this is a quick follow-up answer, not "
            "a report — 1-2 sentences, straight to the point."
        ),
        user_message=json.dumps(
            {"question": body.query, "primary_shipment": primary_data, "related_shipments": related_data},
            default=str,
        ),
        tool_name=_ASK_TOOL_NAME,
        tool_schema=_ASK_TOOL_SCHEMA,
    )
    if not result or not result.get("answer"):
        raise HTTPException(status_code=503, detail="AI answer is unavailable right now")

    return {
        "tracking_id": tracking_id,
        "answer": result["answer"],
        "follow_up_suggestions": result.get("follow_up_suggestions") or [],
    }


# Whitelisted sort columns -> real SQL expression. Never interpolate the
# client-supplied sort key directly into the query string.
_SHIPMENT_SORT_COLUMNS = {
    "last_modified": "s.updated_at",
    "created_at": "s.created_at",
    "estimated_delivery": "s.estimated_delivery",
    "tracking_id": "s.tracking_id",
    "org_name": "c.org_name",
    "current_status": "s.current_status",
}


@app.get("/api/shipments")
def list_shipments(
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    status: str = "",
    delivery_type: str = "",
    is_international: str = "",
    customs_status: str = "",
    sort_by: str = "last_modified",
    sort_dir: str = "desc",
):
    """Paginated, searchable, filterable shipment listing for the dashboard table."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    sort_col = _SHIPMENT_SORT_COLUMNS.get(sort_by, _SHIPMENT_SORT_COLUMNS["last_modified"])
    sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"

    where = []
    params = {}

    if search:
        where.append(
            "(s.tracking_id ILIKE %(search)s OR s.order_id ILIKE %(search)s "
            "OR c.org_name ILIKE %(search)s)"
        )
        params["search"] = f"%{search}%"

    if status:
        where.append("s.current_status = %(status)s")
        params["status"] = status

    if delivery_type:
        where.append("s.delivery_type = %(delivery_type)s")
        params["delivery_type"] = delivery_type

    if is_international in ("true", "false"):
        where.append("s.is_international = %(is_international)s")
        params["is_international"] = is_international == "true"

    if customs_status:
        where.append("s.customs_status = %(customs_status)s")
        params["customs_status"] = customs_status

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*)
            FROM shipments s
            JOIN customers c ON c.customer_id = s.customer_id
            {where_sql};
            """,
            params,
        )
        total = cur.fetchone()["count"]

        cur.execute(
            f"""
            SELECT
              s.tracking_id,
              s.order_id,
              c.org_name,
              c.fedex_account_id,
              s.current_status,
              s.delivery_type,
              s.is_international,
              s.customs_status,
              s.package_type,
              s.package_size,
              s.dest_loc,
              s.src_loc,
              s.estimated_delivery,
              s.delivery_date,
              s.reason_for_delay,
              s.updated_at AS last_modified
            FROM shipments s
            JOIN customers c ON c.customer_id = s.customer_id
            {where_sql}
            ORDER BY {sort_col} {sort_dir}
            LIMIT %(limit)s OFFSET %(offset)s;
            """,
            {**params, "limit": page_size, "offset": offset},
        )
        rows = cur.fetchall()

    return _clean(
        {
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )
