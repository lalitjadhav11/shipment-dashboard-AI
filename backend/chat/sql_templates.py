"""
Stage 4a — Template SQL fill (programmatic, no LLM, the only SQL-generation
path that exists in v0).

Each template is a single, already-safe SELECT statement — no string
concatenation of any kind. Values are bound as psycopg2 parameters, never
interpolated into the SQL text, which is what keeps this stage immune to
injection even before Stage 5's validator runs.

These mirror (but clean up for direct execution) the `recommended_query`
hints already present in 02_phase1_agentic_schema.json's query_patterns.
"""
from dataclasses import dataclass

from . import schema_loader


@dataclass
class FilledTemplate:
    sql: str
    params: dict
    table_key: str  # entity/view key in the schema JSON — feeds Stage 5's allow-list


@dataclass
class TemplateSpec:
    intent: str
    table_key: str
    sql: str
    required: tuple  # ExtractedEntities attribute names that must be present


def _tracking_id_params(entities):
    if not entities.tracking_id:
        return None
    return {"tracking_id": entities.tracking_id}


TEMPLATES = {
    "where_is_my_package": TemplateSpec(
        intent="where_is_my_package",
        table_key="v_shipment_journey_summary",
        sql="""
            SELECT tracking_id, current_status, is_international, customs_status,
                   estimated_delivery, delivery_date, journey_timeline
            FROM v_shipment_journey_summary
            WHERE tracking_id = %(tracking_id)s
        """,
        required=("tracking_id",),
    ),
    "why_is_it_late": TemplateSpec(
        intent="why_is_it_late",
        table_key="v_shipment_journey_summary",
        sql="""
            SELECT tracking_id, current_status, reason_for_delay, delay_comments,
                   estimated_delivery, delivery_date, open_issue_count, journey_timeline
            FROM v_shipment_journey_summary
            WHERE tracking_id = %(tracking_id)s
        """,
        required=("tracking_id",),
    ),
    "customs_status": TemplateSpec(
        intent="customs_status",
        table_key="shipment",
        sql="""
            SELECT tracking_id, is_international, customs_status
            FROM shipments
            WHERE tracking_id = %(tracking_id)s
        """,
        required=("tracking_id",),
    ),
    "open_issues_for_shipment": TemplateSpec(
        intent="open_issues_for_shipment",
        table_key="shipment_issue",
        sql="""
            SELECT issue_id, issue_type, description, status, reported_at, resolved_at
            FROM shipment_issues
            WHERE tracking_id = %(tracking_id)s
              AND status IN ('OPEN', 'INVESTIGATING')
            ORDER BY reported_at DESC
        """,
        required=("tracking_id",),
    ),
    "ops_daily_briefing": TemplateSpec(
        intent="ops_daily_briefing",
        table_key="v_open_issues_summary",
        sql="""
            SELECT issue_type, status, issue_count, avg_age_or_resolution_hours
            FROM v_open_issues_summary
            WHERE status = 'OPEN'
            ORDER BY issue_count DESC
        """,
        required=(),
    ),
    "top_customers_by_volume": TemplateSpec(
        intent="top_customers_by_volume",
        table_key="v_top_customers",
        sql="""
            SELECT org_name, fedex_account_id, shipment_count, delivered_count, on_time_pct
            FROM v_top_customers
        """,
        required=(),
    ),
}

PARAM_BUILDERS = {
    "where_is_my_package": _tracking_id_params,
    "why_is_it_late": _tracking_id_params,
    "customs_status": _tracking_id_params,
    "open_issues_for_shipment": _tracking_id_params,
    "ops_daily_briefing": lambda entities: {},
    "top_customers_by_volume": lambda entities: {},
}


def fill_template(intent: str, entities) -> FilledTemplate | None:
    """Returns None if there's no template for this intent, or if a
    required entity (e.g. tracking_id) wasn't extracted — the caller should
    fall back to a clarifying question (v0) or Stage 4b (v1) in that case."""
    spec = TEMPLATES.get(intent)
    if spec is None:
        return None

    params = PARAM_BUILDERS[intent](entities)
    if params is None:
        return None

    # LIMIT is enforced defensively by Stage 5 too, but templates that can
    # return multiple rows cap themselves explicitly here.
    sql = spec.sql.strip()
    if "LIMIT" not in sql.upper():
        sql += "\nLIMIT 200"

    return FilledTemplate(sql=sql, params=params, table_key=spec.table_key)


def known_intents() -> list:
    return list(TEMPLATES.keys())
