"""
Stage 3 — Schema Scoper (programmatic, no LLM).

Ranks every entity/view in 02_phase1_agentic_schema.json by embedding
similarity to the user query and returns the top-k. This is the key
hallucination-reduction step: in v1, only this narrowed slice of the schema
(not the full ~500-line document) is ever serialized into an LLM prompt.
In v0 it still runs — its output feeds Stage 5's per-request allow-list
even though Stage 4a's templates already know their own table.
"""
import re
from dataclasses import dataclass, field

from . import schema_loader

# 2 was too narrow: for genuinely multi-entity questions (e.g. "group
# shipments by package type and show how many are delayed"), scores 3-9
# often cluster within ~0.02-0.07 of each other — there's no clean runner-up
# gap to cut at. Verified empirically: the raw `shipment` entity (needed for
# package_type/package_size/weight grouping, which no view exposes) ranked
# #4 at 0.465 vs #3 `customer` at 0.469 for that query — effectively a tie
# that top_k=2 would have missed entirely. 4 is the minimum that reliably
# captures it without scoping in the whole schema.
DEFAULT_TOP_K = 4

# Raw, per-record entities to force into scope for identity/list-style
# questions — see wants_individual_records(). Aggregate views group across
# many rows and structurally cannot answer "give me 5 shipments" or "what's
# happening with tracking_id X" even when they score topically similar;
# no amount of embedding-similarity tuning fixes that category of miss,
# because it isn't a topic-relevance question, it's a "does this query want
# one row or a summary of many" question — a different signal entirely.
RECORD_LEVEL_ENTITIES = ["shipment"]

_LIST_PATTERN = re.compile(
    r"\b(?:list|give me|show me|which|show all)\b.{0,20}\b(?:shipments?|packages?|orders?)\b"
    # Live regression (§18): "Give me a breakdown of shipment statuses" matched
    # this pattern purely because "shipment" appears as a modifier before
    # "statuses" — a genuine aggregate request, not a request for individual
    # records — and once wants_individual_records() started actually gating
    # aggregate templates (not just forcing scope), that false match started
    # discarding correct answers. The negative lookahead blocks exactly the
    # "shipment(s)" + aggregate-noun adjacency that signals a breakdown/
    # summary/trend request, while still matching "shipments with status X"
    # (a genuine filtered list — "with"/other words intervene, so the
    # lookahead's \s*-only gap doesn't suppress it). Verified against both
    # classes before committing — see AGENTIC_RAG_ARCHITECTURE.md §18.
    r"(?!\s*(?:status(?:es)?|breakdown|summary|trend|performance|volume|split|mix|rate|percentage)\b)"
    r"|\b\d+\s+(?:shipments?|packages?|orders?)\b",
    re.IGNORECASE,
)

# Same category of miss as RECORD_LEVEL_ENTITIES above, for causal ("why")
# questions specifically: shipment_issue.description is the ONLY entity in
# the schema with actual free-text root-cause explanations (e.g. "Missing HS
# code on declaration; awaiting broker resubmission" for a CUSTOMS_HOLD
# issue) — everything else is enum codes or plain counts. It's topically
# unrelated-scoring for a fleet-wide "why" question (its own field/table
# names are about issue tracking, not about whatever attribute the question
# is asking "why" about), so it loses the top-k ranking to dashboard views
# that only have counts — meaning Stage 4b's LLM never even sees the one
# table that could ground a real answer, and has to fall back to generic
# domain knowledge instead. See AGENTIC_RAG_ARCHITECTURE.md §15.1.
CAUSAL_ENTITIES = ["shipment_issue"]

# Same category of miss again, for "history"/"timeline" questions: live query
# "give me details about 400000000014 and their history of status" reached
# Stage 4b (correctly — no template covers "details + history" together) but
# the LLM drafted a query joining only shipments+customers, never touching
# v_shipment_journey_summary or tracking_events, and its ANSWER THEN CLAIMED
# no historical timeline existed — false; the shipment had 9 logged stages.
# Root cause: neither v_shipment_journey_summary (which already has
# journey_timeline pre-aggregated as a JSONB array — the exact answer to
# "history of status") nor tracking_event scored into the top-k for this
# query; their field/table names are about journey logging, not about
# "details" or "history" as words, so Stage 4b's LLM never even saw either
# existed. See AGENTIC_RAG_ARCHITECTURE.md §20.
HISTORY_ENTITIES = ["v_shipment_journey_summary", "tracking_event"]

# Symmetric opposite of the force-INCLUDE lists above: tables that must
# NEVER be exposed via the natural-language query surface, regardless of how
# they score. shipment_chat_log holds every user's raw user_query/
# ai_response/customer_id — with no auth/session model in Phase 1, anyone
# could ask a plausible-sounding question and read other customers' chat
# history verbatim. Verified live (not hypothetical): it scores into the
# top-4 for entirely ordinary phrasings — including "how is the AI chat
# feature being used", the EXACT example_nl for the legitimate
# chat_activity_summary intent — right alongside its own aggregate view,
# v_chat_activity_summary, which is what should actually answer that
# question. Stage 5's guardrail only checks "is this table in the
# Stage-3-scoped allow-list" — it has no concept of a table that should
# never be allow-listed at all, so this has to be enforced here, before
# scoping even happens (see guardrails.py's FORBIDDEN_TABLES for the
# independent, defense-in-depth backstop). Excluding the raw table doesn't
# affect the legitimate chat_activity_summary v0 template — that template's
# entity_keys is ("v_chat_activity_summary",), a different schema key.
NEVER_SCOPE_ENTITIES = {"shipment_chat_log"}

_HISTORY_QUERY_RE = re.compile(
    r"\bhistor(?:y|ical|ies)\b|\btimeline[s]?\b|\bjourney\b|\bprogression\b"
    r"|\bpast\s+status(?:es)?\b|\bprevious\s+stage[s]?\b|\bstatus\s+histor(?:y|ies)\b"
    r"|\btrack(?:ing)?\s+events?\b",
    re.IGNORECASE,
)


def wants_history(query: str) -> bool:
    """Shared naming/promotion pattern as is_causal_query/wants_individual_records
    above — a query asking for a shipment's status HISTORY needs
    v_shipment_journey_summary/tracking_event force-scoped the same way a
    causal question needs shipment_issue, for the identical reason: the
    relevant table's own vocabulary doesn't overlap with how users phrase
    the question, so plain embedding similarity won't surface it."""
    return bool(_HISTORY_QUERY_RE.search(query))

# Live query: "what are the major blocker for international packages" carries
# the same "explain what's wrong" intent as "why...", but the literal words
# "why"/"reason"/"cause" never appear — verified this slipped through
# uncaught (confidently answered with domestic_vs_international_split, a
# plain count breakdown, same failure shape as §15's original bug). Widened
# past literal causal vocabulary to include synonyms for "what's impeding
# X" — deliberately NOT "block(ed)"/"held (up)" as bare stems, which would
# false-positive on ordinary status descriptions ("shipments blocked from
# delivery", "held up at the hub" — ADJECTIVES describing a shipment's
# state, not a request to explain a cause). Verified against both classes
# before committing — see AGENTIC_RAG_ARCHITECTURE.md §16.1.
#
# A systematic audit across every entity/attribute in the schema (§16.2)
# then found the SAME word families still had gerund/verb forms missing —
# "what is CAUSING failed deliveries" matched none of the patterns above
# (only cause/causes/caused, never "causing") and confidently hit the wrong
# template exactly like the original bug. Rebuilt each family as a full
# inflection group (base/-s/-ed/-ing) instead of a fixed list of forms
# spotted ad hoc, and added "prevent"/"obstruct" — the same "impeding X"
# concept, just not yet seen in a live query. Verified against the same
# true-negative set (status/lookup phrasing using "blocked"/"held up" as
# adjectives) before committing — none of the new inflections reopen that.
_CAUSAL_QUERY_RE = re.compile(
    r"\bwhy\b|\breasons?\b"
    r"|\bcaus(?:e[sd]?|ing)\b"
    r"|\bblock(?:er[s]?|ing)\b"
    r"|\bbottleneck[s]?\b"
    r"|\bobstacle[s]?\b"
    r"|\bobstruct(?:ing|ion[s]?|s)?\b"
    r"|\bimped(?:iment[s]?|ing|e[sd]?)\b"
    r"|\bhinder(?:ing|s)?\b"
    r"|\bprevent(?:ing|s)?\b"
    # Live query: "What does the 'OTHER' category of delays include?" — a
    # DIFFERENT question shape than "why" (asking what a category's contents
    # /definition are, not asking for a cause), but it hit the exact same
    # failure: delay_reason_breakdown confidently answered with the full
    # breakdown table instead of addressing what "OTHER" actually contains.
    # Needs the same fix (force shipment_issue into scope, decline the
    # non-explanatory template) as the causal case, so folded into the same
    # detector rather than building a parallel mechanism — the downstream
    # handling is identical either way. Verified against both classes before
    # committing — see AGENTIC_RAG_ARCHITECTURE.md §16.3.
    r"|\binclude[sd]?\b|\bmakes? up\b|\bfalls? under\b|\bconsists? of\b"
    r"|\bcompris(?:e[sd]?|ing)\b|\bwhat does\b",
    re.IGNORECASE,
)


def is_causal_query(query: str) -> bool:
    """Shared with pipeline.py's post-Stage-4a gate — one definition of
    "this is a 'why' question" for both the entity-forcing use here and the
    template-rejection use there."""
    return bool(_CAUSAL_QUERY_RE.search(query))


# Live bug: "group shipments by package type and show how many are delayed"
# — the exact query AGENTIC_RAG_ARCHITECTURE.md §9 originally used to prove
# Stage 4b's GROUP BY capability — confidently matches delay_reason_breakdown
# (0.634) and answers with a breakdown by REASON, silently ignoring "by
# package type" entirely. Same family as is_causal_query/
# wants_individual_records above (a successfully-filled AGGREGATE template
# is not the right answer if it aggregates along the wrong axis), but a new
# signal: neither is_causal_query nor wants_individual_records fires here
# (verified live — no causal wording, and no identified entity/list phrasing),
# because the mismatch isn't "wrong SHAPE of answer", it's "right shape,
# wrong grouping column."
_GROUP_SIGNAL_RE = re.compile(
    r"\bgroup(?:ed|ing)?\b|\bbroken\s+down\b|\bbreak(?:s|ing)?\s+down\b|\bsplit\b",
    re.IGNORECASE,
)
# Captures the phrase after "by", stopping at "and"/punctuation/end-of-string
# — "group shipments BY PACKAGE TYPE and show how many..." -> "package type".
_GROUP_BY_PHRASE_RE = re.compile(
    r"\bby\s+([a-z][a-z\s]{2,30}?)(?:\s+and\b|\s*[,.?!]|$)",
    re.IGNORECASE,
)

# Canonical dimension name -> phrases that indicate the user asked to group
# by it. Only consulted when _GROUP_SIGNAL_RE already matched, so an ordinary
# query mentioning "by" (e.g. "top customers BY VOLUME") never even reaches
# this — verified live against the golden query set before committing (see
# CHAT_TEST_QUERIES.md's top_customers_by_volume/service_level_mix examples,
# none of which contain a group/split word).
_GROUP_BY_DIMENSION_KEYWORDS = {
    "package_type": ["package type", "package types"],
    "package_size": ["package size", "package sizes"],
    "current_status": ["status"],
    "reason_for_delay": ["delay reason", "reason for delay", "reason", "delay"],
    "shipment_scope": ["international", "domestic", "scope"],
    "delivery_type": ["delivery type", "service level", "delivery types"],
    "customer": ["customer", "org", "shipper", "account"],
    "day": ["day", "date", "daily"],
}

# What each is_aggregate template ACTUALLY groups its rows by — the ground
# truth wants_different_grouping() below compares a detected request against.
# Templates not listed (dashboard_headline, ontime_performance,
# chat_activity_summary, ops_daily_briefing) are single-row or group by a
# fixed pair of columns the user can't meaningfully ask to swap out.
TEMPLATE_GROUPING_DIMENSION = {
    "status_breakdown": "current_status",
    "delay_reason_breakdown": "reason_for_delay",
    "domestic_vs_international_split": "shipment_scope",
    "service_level_mix": "delivery_type",
    "top_customers_by_volume": "customer",
    "daily_volume_trend": "day",
}


def _extract_group_by_dimension(query: str) -> str | None:
    if not _GROUP_SIGNAL_RE.search(query):
        return None
    match = _GROUP_BY_PHRASE_RE.search(query)
    if not match:
        return None
    phrase = match.group(1).lower()
    for dimension, keywords in _GROUP_BY_DIMENSION_KEYWORDS.items():
        if any(kw in phrase for kw in keywords):
            return dimension
    return None


def wants_different_grouping(query: str, resolved_intent: str) -> bool:
    """True when the query explicitly asks to group/break down shipments by
    a dimension that doesn't match what `resolved_intent`'s aggregate
    template actually groups by (including templates not in
    TEMPLATE_GROUPING_DIMENSION at all, e.g. dashboard_headline — those
    can't satisfy ANY explicit grouping request). pipeline.py only consults
    this when the matched template is_aggregate; a non-aggregate template
    match is a different kind of question entirely, not this failure shape."""
    desired = _extract_group_by_dimension(query)
    if desired is None:
        return False
    return TEMPLATE_GROUPING_DIMENSION.get(resolved_intent) != desired


@dataclass
class ScopedSchema:
    entities: list  # ranked entity/view keys, best match first
    scores: dict  # entity/view key -> similarity score
    forced_entities: list = field(default_factory=list)  # added by identity/list
    # signals below, not by ranking — kept separate so the trace can say *why*


def wants_individual_records(query: str, extracted) -> bool:
    """True when the query is about a SPECIFIC identified thing (a
    tracking_id, a named customer, a named city) or explicitly asks to
    list/enumerate records ('give me 5 shipments') — both mean an aggregate
    view is the wrong shape regardless of how topically similar it scores.
    Public (not `_`-prefixed) — same reasoning as is_causal_query above:
    pipeline.py's post-Stage-4a gate needs this exact same signal to decline
    an aggregate-only template match, not just the entity-forcing use here.
    See AGENTIC_RAG_ARCHITECTURE.md §18."""
    if extracted is not None and (extracted.tracking_id or extracted.org_name or extracted.location):
        return True
    return bool(_LIST_PATTERN.search(query))


def scope_schema(query: str, extracted=None, top_k: int = DEFAULT_TOP_K) -> ScopedSchema:
    state = schema_loader.get_state()
    schema_index = state["schema_index"]

    q_vec = schema_loader.embed(query)
    scored = sorted(
        (
            (name, schema_loader.cosine(q_vec, vec))
            for name, vec in schema_index.items()
            if name not in NEVER_SCOPE_ENTITIES
        ),
        key=lambda t: -t[1],
    )
    scored_lookup = dict(scored)
    # Filtered out of `scored`/`scored_lookup` above, not just the final
    # return — this is what stops the RECORD_LEVEL/CAUSAL/HISTORY force-
    # include loops below from ever being able to re-add a never-scope
    # entity via `entity in scored_lookup`.

    ranked = [name for name, _ in scored[:top_k]]
    scores = {name: score for name, score in scored[:top_k]}

    forced = []
    if wants_individual_records(query, extracted):
        for entity in RECORD_LEVEL_ENTITIES:
            if entity not in ranked and entity in scored_lookup:
                ranked.append(entity)
                scores[entity] = scored_lookup[entity]
                forced.append(entity)

    if is_causal_query(query):
        for entity in CAUSAL_ENTITIES:
            if entity not in ranked and entity in scored_lookup:
                ranked.append(entity)
                scores[entity] = scored_lookup[entity]
                forced.append(entity)

    if wants_history(query):
        for entity in HISTORY_ENTITIES:
            if entity not in ranked and entity in scored_lookup:
                ranked.append(entity)
                scores[entity] = scored_lookup[entity]
                forced.append(entity)

    return ScopedSchema(entities=ranked, scores=scores, forced_entities=forced)
