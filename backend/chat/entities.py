"""
Stage 2 — Entity Extractor (programmatic, no LLM).

Pulls concrete values out of the query text so neither the template filler
(Stage 4a) nor a future LLM SQL drafter (Stage 4b, v1) ever has to guess or
invent a tracking ID, enum value, or date.
"""
import re
import time
from dataclasses import dataclass, field

from dateparser.search import search_dates
from rapidfuzz import process, fuzz, utils as fuzz_utils

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
    ("shipment", "package_type"),
    ("shipment", "package_size"),
    ("shipment", "delivery_type"),
    ("shipment_issue", "issue_type"),
    ("shipment_issue", "status"),
]

_ORG_NAME_CACHE = {"names": [], "loaded_at": 0.0}
ORG_NAME_CACHE_TTL_SECONDS = 300

_LOCATION_CACHE = {"names": [], "loaded_at": 0.0}
LOCATION_CACHE_TTL_SECONDS = 300


@dataclass
class ExtractedEntities:
    tracking_id: str | None = None  # first match — kept for template backward-compat
    tracking_ids: list = field(default_factory=list)  # ALL matches — see pipeline.py's
    # multi-shipment guard: no v0 template can answer a "compare X and Y" question, and
    # silently answering about only the first ID is a worse failure than declining.
    enum_matches: dict = field(default_factory=dict)  # field_name -> matched value
    org_name: str | None = None
    location: str | None = None  # city fuzzy-matched from src_loc/dest_loc
    dates: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)  # value -> match score, for the trace


def _extract_tracking_ids(query: str) -> list:
    # dict.fromkeys instead of set() to preserve first-seen order deterministically.
    return list(dict.fromkeys(TRACKING_ID_RE.findall(query)))


def _query_ngrams(query: str, max_n: int = 3) -> list:
    """1-to-3-word windows of the query, lowercased — short, comparable-length
    phrases to test against enum candidates. Two bugs this replaces at once:
    (1) the old regex `[A-Za-z][A-Za-z_ ]{2,}` allows spaces in the character
    class, so on a query with no digits/punctuation to break it up (e.g.
    "show me shipments that are lost") it greedily swallows the ENTIRE query
    into one "token" — comparing a 30-character phrase against a 4-character
    candidate like "LOST" via token_sort_ratio never scores close to 80,
    so enum matching silently never fired for any punctuation-free query.
    (2) matching the whole query against candidates with partial_ratio (the
    substring-search scorer, correct for org_name/location below) causes
    false positives here: "pallet package shipments" scores 83 against
    "LOST_PACKAGE" purely because the word "package" is a literal substring,
    with no relation to the actual concept. Comparing bounded n-grams with
    fuzz.ratio (whole-phrase similarity, not substring search) avoids both."""
    words = re.findall(r"[a-z]+", query.lower())
    return [
        " ".join(words[i:i + n])
        for n in range(1, max_n + 1)
        for i in range(len(words) - n + 1)
    ]


def _extract_enum_matches(query: str, schema: dict) -> dict:
    matches = {}
    ngrams = _query_ngrams(query)
    if not ngrams:
        return matches
    for entity_key, field_name in ENUM_FIELDS:
        allowed = schema["entities"][entity_key]["fields"][field_name].get("allowed_values")
        if not allowed:
            continue
        # "LOST_PACKAGE" -> "lost package" so multi-word enum values line up
        # with how they'd actually be phrased in natural language.
        readable = [v.replace("_", " ").lower() for v in allowed]
        best_score, best_idx = 0.0, None
        for ngram in ngrams:
            hit = process.extractOne(ngram, readable, scorer=fuzz.ratio)
            if hit and hit[1] > best_score:
                best_score, best_idx = hit[1], hit[2]
        if best_idx is not None and best_score >= FUZZY_MATCH_THRESHOLD:
            matches[field_name] = allowed[best_idx]  # original (not readable) form
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
    hit = process.extractOne(query, names, scorer=fuzz.partial_ratio, processor=fuzz_utils.default_process)
    if hit and hit[1] >= FUZZY_MATCH_THRESHOLD:
        return hit[0], hit[1]
    return None, 0.0


def _load_city_names() -> list:
    """Mirrors _load_org_names — src_loc/dest_loc are free-text JSONB, not an
    enum, so there's no allowed_values list to fuzzy-match against; caching
    the real distinct city values from the data is the same trick."""
    now = time.monotonic()
    if now - _LOCATION_CACHE["loaded_at"] < LOCATION_CACHE_TTL_SECONDS and _LOCATION_CACHE["names"]:
        return _LOCATION_CACHE["names"]
    try:
        with get_agent_cursor() as cur:
            cur.execute("""
                SELECT DISTINCT city FROM (
                    SELECT src_loc ->> 'city' AS city FROM shipments
                    UNION
                    SELECT dest_loc ->> 'city' AS city FROM shipments
                ) cities WHERE city IS NOT NULL;
            """)
            names = [row["city"] for row in cur.fetchall()]
        _LOCATION_CACHE["names"] = names
        _LOCATION_CACHE["loaded_at"] = now
    except Exception:
        return _LOCATION_CACHE["names"]
    return names


def _extract_location(query: str) -> tuple:
    names = _load_city_names()
    if not names:
        return None, 0.0
    hit = process.extractOne(query, names, scorer=fuzz.partial_ratio, processor=fuzz_utils.default_process)
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

    location, location_score = _extract_location(query)
    if location:
        result.location = location
        result.scores[location] = location_score

    # dateparser.parse() (the single-value API) requires the ENTIRE input to
    # be a date expression — it returns None for a date embedded in a normal
    # sentence, which is every real query. search_dates() is the substring-
    # extraction API. languages=["en"] matters: without it, dateparser tries
    # every locale it ships and produced a false-positive match on the bare
    # word "me" (interpreted as a date in some non-English locale) inside an
    # unrelated query ("Show me all our large shipments").
    found_dates = search_dates(query, languages=["en"], settings={"PREFER_DATES_FROM": "past"})
    if found_dates:
        result.dates = [d.isoformat() for _, d in found_dates]

    return result
