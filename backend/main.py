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
