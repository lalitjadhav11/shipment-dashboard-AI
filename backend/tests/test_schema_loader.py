"""
schema_loader.rank_by_similarity() — the shared ranking helper extracted out
of intent.py and schema_scope.py (Phase 4 seam, see AGENTIC_RAG_ARCHITECTURE.md
§24). Tested here with synthetic vectors, not the real embedding model —
this is pure cosine-ranking logic, independent of what produced the vectors.
"""
import numpy as np

from chat import schema_loader


def test_rank_by_similarity_orders_best_match_first(monkeypatch):
    # Fake embed() so the "query" vector is deterministic and exactly
    # matches one candidate, partially matches another, and is orthogonal
    # to a third — verifies ranking order without needing the real model.
    monkeypatch.setattr(schema_loader, "embed", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    candidates = {
        "exact_match": np.array([1.0, 0.0], dtype=np.float32),
        "orthogonal": np.array([0.0, 1.0], dtype=np.float32),
        "partial_match": np.array([0.7071, 0.7071], dtype=np.float32),
    }
    ranked = schema_loader.rank_by_similarity("anything", candidates)
    names_in_order = [name for name, _ in ranked]
    assert names_in_order == ["exact_match", "partial_match", "orthogonal"]
    assert ranked[0][1] > ranked[1][1] > ranked[2][1]


def test_rank_by_similarity_empty_candidates_returns_empty():
    assert schema_loader.rank_by_similarity("anything", {}) == []


def test_rank_by_similarity_embeds_query_exactly_once(monkeypatch):
    # Both intent.py and schema_scope.py relied on embedding the query only
    # once and reusing it across every comparison — a regression here would
    # silently multiply embedding cost by the candidate count.
    calls = {"n": 0}

    def counting_embed(text):
        calls["n"] += 1
        return np.array([1.0, 0.0], dtype=np.float32)

    monkeypatch.setattr(schema_loader, "embed", counting_embed)
    candidates = {f"c{i}": np.array([1.0, 0.0], dtype=np.float32) for i in range(10)}
    schema_loader.rank_by_similarity("anything", candidates)
    assert calls["n"] == 1
