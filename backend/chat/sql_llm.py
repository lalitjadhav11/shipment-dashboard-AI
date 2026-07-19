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

from . import intent as intent_stage
from . import llm_client
from . import schema_loader
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


def _build_system_prompt(scoped_entities: list, query: str, available_params: dict) -> str:
    schema_text = schema_loader.describe_entities(scoped_entities)
    examples_text = _few_shot_examples(query)
    params_text = (
        ", ".join(f"%({k})s = {v!r}" for k, v in available_params.items())
        if available_params else "(none extracted from this query)"
    )
    return f"""You are drafting SQL for a shipment tracking system's read-only chat agent.

SCHEMA (the only tables/columns that exist — do not reference anything else):
{schema_text}

AVAILABLE PARAMETERS (reference these by %(name)s in your SQL — never write their values as literals):
{params_text}

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
