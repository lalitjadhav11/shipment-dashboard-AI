"""
Stage 3 — Schema Scoper (programmatic, no LLM).

Ranks every entity/view in 02_phase1_agentic_schema.json by embedding
similarity to the user query and returns the top-k. This is the key
hallucination-reduction step: in v1, only this narrowed slice of the schema
(not the full ~500-line document) is ever serialized into an LLM prompt.
In v0 it still runs — its output feeds Stage 5's per-request allow-list
even though Stage 4a's templates already know their own table.
"""
from dataclasses import dataclass

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


@dataclass
class ScopedSchema:
    entities: list  # ranked entity/view keys, best match first
    scores: dict  # entity/view key -> similarity score


def scope_schema(query: str, top_k: int = DEFAULT_TOP_K) -> ScopedSchema:
    state = schema_loader.get_state()
    schema_index = state["schema_index"]

    q_vec = schema_loader.embed(query)
    scored = sorted(
        ((name, schema_loader.cosine(q_vec, vec)) for name, vec in schema_index.items()),
        key=lambda t: -t[1],
    )

    ranked = [name for name, _ in scored[:top_k]]
    scores = {name: score for name, score in scored[:top_k]}
    return ScopedSchema(entities=ranked, scores=scores)
