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


def classify_intent(query: str) -> IntentResult:
    state = schema_loader.get_state()
    intent_bank = state["intent_bank"]
    if not intent_bank:
        return IntentResult(intent=None, confidence=0.0, matched_example=None)

    q_vec = schema_loader.embed(query)
    best = max(
        intent_bank,
        key=lambda entry: schema_loader.cosine(q_vec, entry["vector"]),
    )
    score = schema_loader.cosine(q_vec, best["vector"])

    if score < CONFIDENCE_THRESHOLD:
        return IntentResult(intent=None, confidence=score, matched_example=best["example_nl"])

    return IntentResult(intent=best["intent"], confidence=score, matched_example=best["example_nl"])
