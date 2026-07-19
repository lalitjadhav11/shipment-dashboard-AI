"""
Stage 2 — Entity Extractor (programmatic, no LLM).

Pulls concrete values out of the query text so neither the template filler
(Stage 4a) nor a future LLM SQL drafter (Stage 4b, v1) ever has to guess or
invent a tracking ID, enum value, or date.
"""
import re
import time
from dataclasses import dataclass, field

import dateparser
from rapidfuzz import process, fuzz

from . import schema_loader
from .db import get_agent_cursor

TRACKING_ID_RE = re.compile(r"\b\d{9,15}\b")
FUZZY_MATCH_THRESHOLD = 80  # rapidfuzz 0-100 scale

# Enum fields worth fuzzy-matching against free text. Keyed by the field name
# the pipeline cares about; sourced from schema allowed_values so this stays
# in sync with the DB automatically.
ENUM_FIELDS = [
    ("shipment", "current_status"),
    ("shipment", "reason_for_delay"),
    ("shipment", "customs_status"),
    ("shipment_issue", "issue_type"),
    ("shipment_issue", "status"),
]

_ORG_NAME_CACHE = {"names": [], "loaded_at": 0.0}
ORG_NAME_CACHE_TTL_SECONDS = 300


@dataclass
class ExtractedEntities:
    tracking_id: str | None = None  # first match — kept for template backward-compat
    tracking_ids: list = field(default_factory=list)  # ALL matches — see pipeline.py's
    # multi-shipment guard: no v0 template can answer a "compare X and Y" question, and
    # silently answering about only the first ID is a worse failure than declining.
    enum_matches: dict = field(default_factory=dict)  # field_name -> matched value
    org_name: str | None = None
    dates: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)  # value -> match score, for the trace


def _extract_tracking_ids(query: str) -> list:
    # dict.fromkeys instead of set() to preserve first-seen order deterministically.
    return list(dict.fromkeys(TRACKING_ID_RE.findall(query)))


def _extract_enum_matches(query: str, schema: dict) -> dict:
    matches = {}
    tokens = re.findall(r"[A-Za-z][A-Za-z_ ]{2,}", query)
    for entity_key, field_name in ENUM_FIELDS:
        allowed = schema["entities"][entity_key]["fields"][field_name].get("allowed_values")
        if not allowed:
            continue
        for token in tokens:
            hit = process.extractOne(token, allowed, scorer=fuzz.token_sort_ratio)
            if hit and hit[1] >= FUZZY_MATCH_THRESHOLD:
                matches[field_name] = hit[0]
    return matches


def _load_org_names() -> list:
    now = time.monotonic()
    if now - _ORG_NAME_CACHE["loaded_at"] < ORG_NAME_CACHE_TTL_SECONDS and _ORG_NAME_CACHE["names"]:
        return _ORG_NAME_CACHE["names"]
    try:
        with get_agent_cursor() as cur:
            cur.execute("SELECT org_name FROM customers;")
            names = [row["org_name"] for row in cur.fetchall()]
        _ORG_NAME_CACHE["names"] = names
        _ORG_NAME_CACHE["loaded_at"] = now
    except Exception:
        # Customer cache is a nice-to-have (org_name disambiguation); a DB
        # hiccup here shouldn't break tracking-id-based intents.
        return _ORG_NAME_CACHE["names"]
    return names


def _extract_org_name(query: str) -> tuple:
    names = _load_org_names()
    if not names:
        return None, 0.0
    hit = process.extractOne(query, names, scorer=fuzz.partial_ratio)
    if hit and hit[1] >= FUZZY_MATCH_THRESHOLD:
        return hit[0], hit[1]
    return None, 0.0


def extract_entities(query: str) -> ExtractedEntities:
    schema = schema_loader.get_state()["raw"]
    result = ExtractedEntities()

    result.tracking_ids = _extract_tracking_ids(query)
    result.tracking_id = result.tracking_ids[0] if result.tracking_ids else None
    result.enum_matches = _extract_enum_matches(query, schema)

    org_name, org_score = _extract_org_name(query)
    if org_name:
        result.org_name = org_name
        result.scores[org_name] = org_score

    parsed_date = dateparser.parse(query, settings={"PREFER_DATES_FROM": "past"})
    if parsed_date:
        result.dates.append(parsed_date.isoformat())

    return result
