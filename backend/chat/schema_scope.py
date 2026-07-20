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
# questions — see _wants_individual_records(). Aggregate views group across
# many rows and structurally cannot answer "give me 5 shipments" or "what's
# happening with tracking_id X" even when they score topically similar;
# no amount of embedding-similarity tuning fixes that category of miss,
# because it isn't a topic-relevance question, it's a "does this query want
# one row or a summary of many" question — a different signal entirely.
RECORD_LEVEL_ENTITIES = ["shipment"]

_LIST_PATTERN = re.compile(
    r"\b(?:list|give me|show me|which|show all)\b.{0,20}\b(?:shipments?|packages?|orders?)\b"
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


@dataclass
class ScopedSchema:
    entities: list  # ranked entity/view keys, best match first
    scores: dict  # entity/view key -> similarity score
    forced_entities: list = field(default_factory=list)  # added by identity/list
    # signals below, not by ranking — kept separate so the trace can say *why*


def _wants_individual_records(query: str, extracted) -> bool:
    """True when the query is about a SPECIFIC identified thing (a
    tracking_id, a named customer, a named city) or explicitly asks to
    list/enumerate records ('give me 5 shipments') — both mean an aggregate
    view is the wrong shape regardless of how topically similar it scores."""
    if extracted is not None and (extracted.tracking_id or extracted.org_name or extracted.location):
        return True
    return bool(_LIST_PATTERN.search(query))


def scope_schema(query: str, extracted=None, top_k: int = DEFAULT_TOP_K) -> ScopedSchema:
    state = schema_loader.get_state()
    schema_index = state["schema_index"]

    q_vec = schema_loader.embed(query)
    scored = sorted(
        ((name, schema_loader.cosine(q_vec, vec)) for name, vec in schema_index.items()),
        key=lambda t: -t[1],
    )
    scored_lookup = dict(scored)

    ranked = [name for name, _ in scored[:top_k]]
    scores = {name: score for name, score in scored[:top_k]}

    forced = []
    if _wants_individual_records(query, extracted):
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

    return ScopedSchema(entities=ranked, scores=scores, forced_entities=forced)
