"""
Loads 02_phase1_agentic_schema.json once at process start and precomputes the
embedding indexes shared by Stage 1 (intent) and Stage 3 (schema scoping).

This is the single source of truth described in AGENTIC_RAG_ARCHITECTURE.md —
every other chat/ module reads schema knowledge through this file instead of
hardcoding table/column names, so Phase 2+ growth is a data change here, not
a code change elsewhere.
"""
import os
import json
import re
import threading

import numpy as np
from sentence_transformers import SentenceTransformer

SCHEMA_PATH = os.environ.get(
    "AGENT_SCHEMA_PATH",
    os.path.join(os.path.dirname(__file__), "..", "schema", "02_phase1_agentic_schema.json"),
)
EMBEDDING_MODEL_NAME = os.environ.get("AGENT_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

_lock = threading.Lock()
_state = {}


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _get_model() -> SentenceTransformer:
    if "model" not in _state:
        _state["model"] = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
    return _state["model"]


def embed(text: str) -> np.ndarray:
    vec = _get_model().encode(text, normalize_embeddings=True)
    return np.asarray(vec, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    # Vectors are pre-normalized by encode(normalize_embeddings=True), so the
    # dot product alone is the cosine similarity.
    return float(np.dot(a, b))


_COLUMN_NAME_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)")


def _bare_column_name(raw: str) -> str:
    """View 'columns' entries sometimes carry trailing description text,
    e.g. 'journey_timeline (JSONB array of ...)' — keep just the identifier."""
    m = _COLUMN_NAME_RE.match(raw.strip())
    return m.group(1) if m else raw.strip()


def _build_indexes(schema: dict) -> dict:
    intent_bank = [
        {
            "intent": p["intent"],
            "example_nl": p["example_nl"],
            "vector": embed(p["example_nl"]),
        }
        for p in schema["agent_context"]["query_patterns"]
    ]

    schema_objects = {**schema["entities"], **schema["views"]}
    schema_index = {}
    field_names = {}
    table_names = {}

    for name, obj in schema_objects.items():
        if "fields" in obj:  # entity
            fields = list(obj["fields"].keys())
            table_names[name] = obj["table"]
        else:  # view
            fields = [_bare_column_name(c) for c in obj["columns"]]
            table_names[name] = name  # view dict key IS the real view name

        field_names[name] = set(fields)
        # Entities use "description"; views use "purpose" — normalize both.
        blurb = obj.get("description") or obj.get("purpose") or ""
        text = f"{blurb} fields: {', '.join(fields)}"
        schema_index[name] = embed(text)

    return {
        "raw": schema,
        "intent_bank": intent_bank,
        "schema_index": schema_index,
        "field_names": field_names,
        "table_names": table_names,
    }


def get_state() -> dict:
    """Lazily builds (once) and returns the shared schema/embedding state."""
    if "indexes" not in _state:
        with _lock:
            if "indexes" not in _state:  # re-check inside the lock
                schema = _load_schema()
                _state["indexes"] = _build_indexes(schema)
    return _state["indexes"]


def schema_fields(name: str) -> set:
    """Bare column/field names for an entity or view key (e.g. 'shipment')."""
    return get_state()["field_names"].get(name, set())


def table_name(name: str) -> str:
    """Real DB table/view name for an entity or view key."""
    return get_state()["table_names"].get(name, name)


def warm_up() -> None:
    """Call at FastAPI startup so the first user request isn't the one
    paying for the model load + embedding precompute."""
    get_state()
