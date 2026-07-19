"""
Phase 1 Shipment Dashboard — Backend API (boilerplate).

A deliberately small FastAPI app that proves the full stack is wired together:
  GET /                 -> hello world
  GET /health           -> liveness + DB connectivity
  GET /api/summary      -> the v_dashboard_headline snapshot (10 KPIs, one query)
  GET /api/status-breakdown -> v_status_breakdown

Extend from here for the real dashboard + AI chat endpoints.
"""
import os
from contextlib import contextmanager
from decimal import Decimal
from datetime import datetime, date, time

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from chat.router import router as chat_router
from chat import schema_loader

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
