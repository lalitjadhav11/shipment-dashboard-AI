"""
Stage 1 — Intent Classifier (programmatic, no LLM).

Embeds the user query and compares it against the example_nl few-shot bank
built from 02_phase1_agentic_schema.json's query_patterns. Growing the intent
set is a JSON edit, not a code change.
"""
from dataclasses import dataclass

from . import schema_loader

# Tuned empirically against the live model, not guessed. Measured true
# negatives (out-of-domain: "what's the weather", "reset my password", ...)
# topped out at 0.304. Measured legitimate paraphrases of known intents
# ("has 800000000131 cleared customs yet", "my order seems stuck, what's
# happening") that a 0.55 threshold was wrongly declining started at 0.455.
# 0.40 sits in that ~0.15 gap with margin on both sides — see
# AGENTIC_RAG_ARCHITECTURE.md's corner-case audit for the full test matrix.
CONFIDENCE_THRESHOLD = 0.40


@dataclass
class IntentResult:
    intent: str | None
    confidence: float
    matched_example: str | None


@dataclass
class RankedIntent:
    intent: str
    example_nl: str
    score: float


def rank_intents(query: str) -> list:
    """Every known intent, ranked by similarity to `query`, best first.
    classify_intent() only ever looks at index 0 (v0). Stage 4b (v1) reuses
    this same computation to pick few-shot example templates from indices
    1-3 — see AGENTIC_RAG_ARCHITECTURE.md §4 Stage 1's note on this."""
    state = schema_loader.get_state()
    intent_bank = state["intent_bank"]
    if not intent_bank:
        return []

    q_vec = schema_loader.embed(query)
    ranked = sorted(
        (
            RankedIntent(entry["intent"], entry["example_nl"], schema_loader.cosine(q_vec, entry["vector"]))
            for entry in intent_bank
        ),
        key=lambda r: -r.score,
    )
    return ranked


def classify_intent(query: str) -> IntentResult:
    ranked = rank_intents(query)
    if not ranked:
        return IntentResult(intent=None, confidence=0.0, matched_example=None)

    best = ranked[0]
    if best.score < CONFIDENCE_THRESHOLD:
        return IntentResult(intent=None, confidence=best.score, matched_example=best.example_nl)

    return IntentResult(intent=best.intent, confidence=best.score, matched_example=best.example_nl)
