"""
Stage 4b — LLM SQL draft (v1 addition, fallback only).

Reached only when Stage 4a (sql_templates.fill_template) returns None — no
known template matched, or a required entity was missing. Waterfall, not
merge: pipeline.py always tries Stage 4a first; this stage never runs for
the 6 known intents. See AGENTIC_RAG_ARCHITECTURE.md §4 Stage 4b.

Three inputs go into the LLM call, all filtered to relevance — never the
full schema, never the full template library:
  1. the user's query
  2. the Stage-3-scoped schema slice (= Stage 5's allow-list surface)
  3. the 2-3 nearest existing templates as few-shot examples, reusing
     Stage 1's full ranking (classify_intent only reads index 0; this is
     the second consumer of that same computation)

The LLM never sees or invents literal values for anything the user typed —
it only writes %(name)s placeholders against a fixed set of names Stage 2
already extracted. The actual values are bound by this module from
`extracted`, never from the LLM's output, so this stage can't smuggle a
value through free text the way literal SQL interpolation could.
"""
import re
from datetime import datetime, timezone

from . import intent as intent_stage
from . import llm_client
from . import schema_loader
from . import schema_scope
from .sql_templates import FilledTemplate, TEMPLATES

FEW_SHOT_COUNT = 3
_PLACEHOLDER_RE = re.compile(r"%\((\w+)\)s")

TOOL_NAME = "draft_sql_query"
TOOL_SCHEMA = {
    "description": (
        "Draft exactly one read-only PostgreSQL SELECT statement, using ONLY the "
        "tables/columns listed in the schema, to answer the user's question."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single SELECT statement. Use %(name)s placeholders (psycopg2 "
                    "pyformat style) for any value the user supplied that matches one "
                    "of the listed available parameter names — never write that value "
                    "as a literal. Always include a LIMIT (200 max)."
                ),
            },
            "explanation": {
                "type": "string",
                "description": "One sentence explaining what this query computes.",
            },
        },
        "required": ["sql", "explanation"],
    },
}


def _available_params(entities) -> dict:
    """Values Stage 2 already extracted from the query text — the ONLY
    values the LLM may reference, and only via placeholder name, never as a
    literal it invents itself."""
    params = {}
    if entities.tracking_id:
        params["tracking_id"] = entities.tracking_id
    if entities.org_name:
        params["org_name"] = entities.org_name
    if entities.dates:
        params["date"] = entities.dates[0]
    params.update(entities.enum_matches)
    return params


def _few_shot_examples(query: str) -> str:
    ranked = intent_stage.rank_intents(query)[:FEW_SHOT_COUNT]
    blocks = []
    for r in ranked:
        spec = TEMPLATES.get(r.intent)
        if spec is None:
            continue
        blocks.append(f'Q: "{r.example_nl}"\nSQL: {spec.sql.strip()}')
    return "\n\n".join(blocks)


def _causal_guidance(scoped_entities: list, query: str) -> str:
    """schema_scope.py force-includes shipment_issue for causal ("why")
    queries specifically because it's the only entity with real root-cause
    text (issue_type + description) — but merely being IN the schema slice
    wasn't enough of a signal on its own: verified live that the LLM still
    picked a count-only view (v_domestic_vs_international) over
    shipment_issues even with both available, because nothing told it WHY
    that table mattered more for this kind of question. See
    AGENTIC_RAG_ARCHITECTURE.md §15.1."""
    if not schema_scope.is_causal_query(query) or "shipment_issue" not in scoped_entities:
        return ""
    return (
        "\nThis is a \"why\"/root-cause question, and shipment_issues is available above — "
        "PREFER querying its issue_type and description columns over any count-only view. "
        "description holds the actual real-world explanation (e.g. \"Missing HS code on "
        "declaration; awaiting broker resubmission\"); a count or percentage from a dashboard "
        "view can show the SIZE of a pattern but never its cause. If the question is fleet-wide "
        "(no tracking_id), aggregate/sample descriptions grouped by issue_type rather than "
        "returning only a count.\n"
    )


def _history_guidance(scoped_entities: list, query: str) -> str:
    """Same lesson as _causal_guidance above, same section it's documented
    in (AGENTIC_RAG_ARCHITECTURE.md §20): schema_scope.py force-includes
    v_shipment_journey_summary/tracking_event for "history"/"timeline"
    questions, but forcing an entity into the schema slice doesn't mean the
    LLM will actually reach for it — live query "give me details about
    400000000014 and their history of status" had the entity forced in but
    still drafted shipments+customers only, then falsely claimed no
    historical timeline existed in the data at all."""
    if not schema_scope.wants_history(query):
        return ""
    if "v_shipment_journey_summary" not in scoped_entities and "tracking_event" not in scoped_entities:
        return ""
    return (
        "\nThis question asks about STATUS HISTORY/TIMELINE, and journey data is available "
        "above — PREFER v_shipment_journey_summary's journey_timeline column (a JSONB array "
        "of every stage transition with timestamps) over querying shipments alone, which only "
        "has the CURRENT status. If tracking_events is what's in scope instead, select its "
        "full stage/location/event_timestamp/notes history ordered by event_timestamp. Never "
        "claim historical data isn't available without first checking whether one of these two "
        "sources was actually queried.\n"
    )


def _build_system_prompt(scoped_entities: list, query: str, available_params: dict) -> str:
    schema_text = schema_loader.describe_entities(scoped_entities)
    examples_text = _few_shot_examples(query)
    causal_text = _causal_guidance(scoped_entities, query)
    history_text = _history_guidance(scoped_entities, query)
    params_text = (
        ", ".join(f"%({k})s = {v!r}" for k, v in available_params.items())
        if available_params else "(none extracted from this query)"
    )
    return f"""You are drafting SQL for a shipment tracking system's read-only chat agent.

CURRENT DATE/TIME: {datetime.now(timezone.utc).isoformat()}

SCHEMA (the only tables/columns that exist — do not reference anything else):
{schema_text}
{causal_text}{history_text}
AVAILABLE PARAMETERS (reference these by %(name)s in your SQL — never write their values as literals):
{params_text}
NOTE on %(date)s specifically, if listed above: it is already the FULLY-RESOLVED absolute
timestamp implied by whatever relative phrase the user used ("more than a week" -> already
resolved to exactly that cutoff, i.e. now minus 7 days). Use it DIRECTLY in a comparison
(reported_at < %(date)s::timestamptz) — do NOT subtract or add another INTERVAL to it, that
double-applies the offset the resolution already did. Only combine it with an INTERVAL if the
user's question describes a second, independent offset from that already-resolved date.

EXAMPLE QUERIES FOR SIMILAR QUESTIONS (style/convention reference, not necessarily the right table for THIS question):
{examples_text}

Rules:
- SELECT only. No INSERT/UPDATE/DELETE/DROP/ALTER/CREATE.
- Exactly one statement.
- Only reference the tables/columns listed in SCHEMA above.
- Use %(name)s placeholders for anything from AVAILABLE PARAMETERS — never inline that value.
- Always include LIMIT (200 max).
- String comparisons are case-sensitive. Category/status/enum-like values in this schema are
  UPPERCASE (e.g. 'INTERNATIONAL', 'DELIVERED', 'CUSTOMS') unless SCHEMA explicitly says
  otherwise for that field — never guess lowercase.
- %(date)s (if listed above) is an ISO 8601 timestamp string with no type information attached.
  Postgres cannot infer its type from a bare placeholder, so comparing it against a timestamp
  column without an explicit cast fails with "invalid input syntax for type interval" (Postgres
  guesses the wrong operator overload for an untyped operand). Always write %(date)s::timestamptz
  — e.g. `reported_at < %(date)s::timestamptz`, not `reported_at < %(date)s`. See the NOTE above
  AVAILABLE PARAMETERS for why no further INTERVAL arithmetic belongs on this specific cast.

You MUST respond by calling the {TOOL_NAME} function — never respond with plain text,
explanation, or reasoning outside of it. Call the function now."""


def _validate_placeholders(sql: str, available_params: dict) -> bool:
    """Defensive pre-check: every %(name)s the LLM wrote must be a name we
    actually have a bound value for, or execution would fail with a raw
    psycopg2 KeyError instead of a clean Stage 4b decline."""
    referenced = set(_PLACEHOLDER_RE.findall(sql))
    return referenced <= set(available_params.keys())


def draft_sql(query: str, scoped_entities: list, entities) -> FilledTemplate | None:
    if not scoped_entities:
        return None

    available_params = _available_params(entities)
    system_prompt = _build_system_prompt(scoped_entities, query, available_params)

    result = llm_client.call_tool(
        system_prompt=system_prompt,
        user_message=query,
        tool_name=TOOL_NAME,
        tool_schema=TOOL_SCHEMA,
    )
    if result is None or not result.get("sql"):
        return None

    sql = result["sql"].strip()
    if not _validate_placeholders(sql, available_params):
        return None

    if "LIMIT" not in sql.upper():
        sql += "\nLIMIT 200"

    return FilledTemplate(sql=sql, params=available_params, entity_keys=scoped_entities, source="llm")
