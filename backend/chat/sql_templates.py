"""
Stage 4a — Template SQL fill (programmatic, no LLM, the preferred SQL-
generation path — always tried before Stage 4b, v1's LLM fallback).

Each template is a single, already-safe SELECT statement — no string
concatenation of any kind. Values are bound as psycopg2 parameters, never
interpolated into the SQL text, which is what keeps this stage immune to
injection even before Stage 5's validator runs.

These mirror (but clean up for direct execution) the `recommended_query`
hints already present in 02_phase1_agentic_schema.json's query_patterns.

20 templates in two groups:
  - 8 zero-param templates, one per previously-untemplated dashboard view
    (dashboard_headline, status_breakdown, ontime_performance,
    delay_reason_breakdown, domestic_vs_international_split,
    daily_volume_trend, service_level_mix, chat_activity_summary) — these
    were already-built views nobody had wired a template to yet.
  - 6 "mix and match" parameterized templates using entities Stage 2 already
    extracts (org_name, enum-matched current_status/package_type/
    delivery_type) — genuine multi-condition/JOIN queries, not just
    single-tracking-ID lookups.
"""
from dataclasses import dataclass

from . import schema_loader


@dataclass
class FilledTemplate:
    sql: str
    params: dict
    entity_keys: list  # schema JSON entity/view key(s) — feeds Stage 5's allow-list.
    # A list, not a single key, so Stage 4a's (often one table, sometimes a
    # JOIN) and Stage 4b's (v1 — an LLM-scoped set) output share one shape
    # and pipeline.py doesn't need to special-case which stage produced it.
    source: str = "template"  # "template" | "llm" — carried into the trace and
    # into Stage 7's routing decision (§4 Stage 7 in AGENTIC_RAG_ARCHITECTURE.md)


@dataclass
class TemplateSpec:
    intent: str
    entity_keys: tuple  # one per table/view this template's SQL touches
    sql: str
    required: tuple  # ExtractedEntities attribute names that must be present


def _tracking_id_params(entities):
    if not entities.tracking_id:
        return None
    return {"tracking_id": entities.tracking_id}


def _org_name_params(entities):
    if not entities.org_name:
        return None
    return {"org_name": entities.org_name}


def _enum_param_builder(field_name):
    """Factory: returns a param builder pulling one specific enum-matched
    field out of Stage 2's enum_matches dict, or None if that field wasn't
    matched in this query — same missing-required-entity contract as every
    other param builder here."""
    def build(entities):
        value = entities.enum_matches.get(field_name)
        if not value:
            return None
        return {field_name: value}
    return build


TEMPLATES = {
    # --- Single-shipment lookups (original 6) ---------------------------
    "where_is_my_package": TemplateSpec(
        intent="where_is_my_package",
        entity_keys=("v_shipment_journey_summary",),
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
        entity_keys=("v_shipment_journey_summary",),
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
        entity_keys=("shipment",),
        sql="""
            SELECT tracking_id, is_international, customs_status
            FROM shipments
            WHERE tracking_id = %(tracking_id)s
        """,
        required=("tracking_id",),
    ),
    "open_issues_for_shipment": TemplateSpec(
        intent="open_issues_for_shipment",
        entity_keys=("shipment_issue",),
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
        entity_keys=("v_open_issues_summary",),
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
        entity_keys=("v_top_customers",),
        sql="""
            SELECT org_name, fedex_account_id, shipment_count, delivered_count, on_time_pct
            FROM v_top_customers
        """,
        required=(),
    ),

    # --- Zero-param views (8 new — every previously-unwired dashboard view) ---
    "dashboard_headline": TemplateSpec(
        intent="dashboard_headline",
        entity_keys=("v_dashboard_headline",),
        sql="""
            SELECT total_shipments, total_customers, delivered_count, on_time_pct,
                   in_transit_overdue, open_issues, international_shipments,
                   customs_held_now, lost_count, returned_count, cancelled_count
            FROM v_dashboard_headline
        """,
        required=(),
    ),
    "status_breakdown": TemplateSpec(
        intent="status_breakdown",
        entity_keys=("v_status_breakdown",),
        sql="""
            SELECT current_status, shipment_count, pct_of_total
            FROM v_status_breakdown
            ORDER BY shipment_count DESC
        """,
        required=(),
    ),
    "ontime_performance": TemplateSpec(
        intent="ontime_performance",
        entity_keys=("v_ontime_performance",),
        sql="""
            SELECT delivered_count, delivered_on_time, delivered_late, on_time_pct,
                   avg_delay_hours_when_late, in_transit_overdue
            FROM v_ontime_performance
        """,
        required=(),
    ),
    "delay_reason_breakdown": TemplateSpec(
        intent="delay_reason_breakdown",
        entity_keys=("v_delay_reason_breakdown",),
        sql="""
            SELECT reason_for_delay, shipment_count, pct_of_delayed
            FROM v_delay_reason_breakdown
            ORDER BY shipment_count DESC
        """,
        required=(),
    ),
    "domestic_vs_international_split": TemplateSpec(
        intent="domestic_vs_international_split",
        entity_keys=("v_domestic_vs_international",),
        sql="""
            SELECT shipment_scope, shipment_count, customs_held, customs_pending, customs_cleared
            FROM v_domestic_vs_international
        """,
        required=(),
    ),
    "daily_volume_trend": TemplateSpec(
        # NOT v_daily_volume_trend — that view's unbounded generate_series
        # (full min-to-max date range in the data) LEFT JOINs the entire
        # shipments table twice per day with no index-usable join condition;
        # measured at ~12.7s against the seeded 25k-row dataset, reliably
        # exceeding agent_ro's statement_timeout. This bounded, two-subquery
        # rewrite answers the same "recent volume trend" question — each
        # subquery is an independent indexed range scan on created_at /
        # delivery_date, measured at ~20ms. See AGENTIC_RAG_ARCHITECTURE.md
        # §9 for the full incident (this is also what motivated generalizing
        # the guardrail's alias check to nested subqueries, not just the
        # outermost SELECT).
        intent="daily_volume_trend",
        entity_keys=("shipment",),
        sql="""
            SELECT COALESCE(c.day, d.day) AS day,
                   COALESCE(c.cnt, 0) AS shipments_created,
                   COALESCE(d.cnt, 0) AS shipments_delivered
            FROM (
                SELECT created_at::date AS day, count(*) AS cnt
                FROM shipments
                WHERE created_at >= CURRENT_DATE - 14
                GROUP BY created_at::date
            ) c
            FULL OUTER JOIN (
                SELECT delivery_date::date AS day, count(*) AS cnt
                FROM shipments
                WHERE delivery_date >= CURRENT_DATE - 14
                GROUP BY delivery_date::date
            ) d ON d.day = c.day
            ORDER BY day DESC
            LIMIT 14
        """,
        required=(),
    ),
    "service_level_mix": TemplateSpec(
        intent="service_level_mix",
        entity_keys=("v_service_level_mix",),
        sql="""
            SELECT delivery_type, shipment_count, on_time_pct
            FROM v_service_level_mix
            ORDER BY shipment_count DESC
        """,
        required=(),
    ),
    "chat_activity_summary": TemplateSpec(
        intent="chat_activity_summary",
        entity_keys=("v_chat_activity_summary",),
        sql="""
            SELECT total_chat_interactions, shipments_with_chat, avg_confidence,
                   low_confidence_needing_review
            FROM v_chat_activity_summary
        """,
        required=(),
    ),

    # --- Mix-and-match filtered/joined templates (6 new) -----------------
    "shipments_by_customer": TemplateSpec(
        intent="shipments_by_customer",
        entity_keys=("shipment", "customer"),
        sql="""
            SELECT s.tracking_id, s.current_status, s.reason_for_delay,
                   s.estimated_delivery, s.delivery_date
            FROM shipments s
            JOIN customers c ON c.customer_id = s.customer_id
            WHERE c.org_name = %(org_name)s
            ORDER BY s.created_at DESC
            LIMIT 20
        """,
        required=("org_name",),
    ),
    "shipments_by_customer_delayed": TemplateSpec(
        intent="shipments_by_customer_delayed",
        entity_keys=("shipment", "customer"),
        sql="""
            SELECT s.tracking_id, s.current_status, s.reason_for_delay, s.delay_comments
            FROM shipments s
            JOIN customers c ON c.customer_id = s.customer_id
            WHERE c.org_name = %(org_name)s
              AND s.reason_for_delay <> 'NONE'
            ORDER BY s.created_at DESC
            LIMIT 20
        """,
        required=("org_name",),
    ),
    "shipments_by_status": TemplateSpec(
        intent="shipments_by_status",
        entity_keys=("shipment",),
        sql="""
            SELECT tracking_id, current_status, reason_for_delay, estimated_delivery
            FROM shipments
            WHERE current_status = %(current_status)s
            ORDER BY updated_at DESC
            LIMIT 20
        """,
        required=("current_status",),
    ),
    "shipments_by_package_type": TemplateSpec(
        intent="shipments_by_package_type",
        entity_keys=("shipment",),
        sql="""
            SELECT tracking_id, current_status, package_type, package_size
            FROM shipments
            WHERE package_type = %(package_type)s
            ORDER BY created_at DESC
            LIMIT 20
        """,
        required=("package_type",),
    ),
    "shipments_by_delivery_type": TemplateSpec(
        intent="shipments_by_delivery_type",
        entity_keys=("shipment",),
        sql="""
            SELECT tracking_id, current_status, delivery_type, estimated_delivery
            FROM shipments
            WHERE delivery_type = %(delivery_type)s
            ORDER BY created_at DESC
            LIMIT 20
        """,
        required=("delivery_type",),
    ),
    "failed_delivery_shipments": TemplateSpec(
        intent="failed_delivery_shipments",
        entity_keys=("shipment",),
        sql="""
            SELECT tracking_id, current_status, failed_delivery_attempts, last_delivery_attempt_at
            FROM shipments
            WHERE failed_delivery_attempts > 0
            ORDER BY failed_delivery_attempts DESC
            LIMIT 20
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
    "dashboard_headline": lambda entities: {},
    "status_breakdown": lambda entities: {},
    "ontime_performance": lambda entities: {},
    "delay_reason_breakdown": lambda entities: {},
    "domestic_vs_international_split": lambda entities: {},
    "daily_volume_trend": lambda entities: {},
    "service_level_mix": lambda entities: {},
    "chat_activity_summary": lambda entities: {},
    "shipments_by_customer": _org_name_params,
    "shipments_by_customer_delayed": _org_name_params,
    "shipments_by_status": _enum_param_builder("current_status"),
    "shipments_by_package_type": _enum_param_builder("package_type"),
    "shipments_by_delivery_type": _enum_param_builder("delivery_type"),
    "failed_delivery_shipments": lambda entities: {},
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

    return FilledTemplate(sql=sql, params=params, entity_keys=list(spec.entity_keys), source="template")


def known_intents() -> list:
    return list(TEMPLATES.keys())
