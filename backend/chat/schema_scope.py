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

    return ScopedSchema(entities=ranked, scores=scores, forced_entities=forced)
