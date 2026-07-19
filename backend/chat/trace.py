"""
SSE event formatting for the "thinking" trace described in
AGENTIC_RAG_ARCHITECTURE.md §5. verbose gating (who is allowed to see the
intermediate stages, versus only the final answer) lives in router.py, not
here — this module only knows how to format an event, not who receives it.
"""
import json


def sse_event(stage: str, detail: dict) -> str:
    payload = {"stage": stage, "detail": detail}
    return f"data: {json.dumps(payload)}\n\n"
