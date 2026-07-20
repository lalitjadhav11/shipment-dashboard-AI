"""
Shared LLM client + a generic structured-output ("tool-use") helper for the
two v1 LLM touchpoints — Stage 4b (sql_llm.py) and Stage 7 v1
(synthesize.py). Both call `call_tool()` without knowing or caring which
provider is active, so retry/error handling and provider selection live in
one place.

Three providers, switched by AGENT_LLM_PROVIDER (default "anthropic"):
  - "anthropic": Claude via the Anthropic API (needs ANTHROPIC_API_KEY, real cost/latency)
  - "gemini":    Gemini via the Google GenAI API (needs GEMINI_API_KEY — has a
                 free tier, see https://aistudio.google.com/apikey)
  - "ollama":    a local model via Ollama (needs `ollama pull <model>` on the
                 host and a model that supports tool/function calling —
                 e.g. llama3.1, llama3.2, qwen2.5, mistral-nemo)
All three implement the exact same call_tool() contract, so
sql_llm.py/synthesize.py never branch on provider — switching is a single env
var, no code change.

Design rule carried over from the architecture doc: an LLM failure (missing
key/host unreachable, network error, malformed response, rate limit) must
never crash the pipeline — it degrades to "this stage couldn't help," and the
caller falls back to the next thing in the waterfall (v0's clarifying
answer). This module never raises; it returns None on any failure and logs
why.
"""
import os
import sys

PROVIDER = os.environ.get("AGENT_LLM_PROVIDER", "anthropic").lower()

ANTHROPIC_MODEL = os.environ.get("AGENT_LLM_MODEL", "claude-haiku-4-5-20251001")
# gemini-2.5-flash and gemini-2.0-flash both returned "not available to new
# users" / zero free-tier quota when tested against a fresh API key — the
# 3.x preview line is what's actually reachable on a new project's free tier
# right now. Revisit if Google reopens free-tier quota on the stable models.
GEMINI_MODEL = os.environ.get("AGENT_GEMINI_MODEL", "gemini-3-flash-preview")
OLLAMA_MODEL = os.environ.get("AGENT_OLLAMA_MODEL", "llama3.1")
OLLAMA_HOST = os.environ.get("AGENT_OLLAMA_HOST", "http://host.docker.internal:11434")
MAX_TOKENS = 1024

_client_state = {}


# --- Anthropic -----------------------------------------------------------

def _get_anthropic_client():
    if "anthropic" not in _client_state:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            _client_state["anthropic"] = None
        else:
            import anthropic
            _client_state["anthropic"] = anthropic.Anthropic(api_key=api_key)
    return _client_state["anthropic"]


def _call_anthropic(system_prompt: str, user_message: str, tool_name: str, tool_schema: dict) -> dict | None:
    client = _get_anthropic_client()
    if client is None:
        print("[chat] ANTHROPIC_API_KEY not set — LLM stage unavailable, falling back", file=sys.stderr)
        return None

    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[{
                "name": tool_name,
                "description": tool_schema.get("description", ""),
                "input_schema": tool_schema["input_schema"],
            }],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:  # noqa: BLE001 — any SDK/network failure degrades gracefully
        print(f"[chat] Anthropic call failed ({tool_name}): {exc}", file=sys.stderr)
        return None

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input

    print(f"[chat] Anthropic response had no {tool_name} tool_use block", file=sys.stderr)
    return None


# --- Gemini ----------------------------------------------------------------

def _get_gemini_client():
    if "gemini" not in _client_state:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            _client_state["gemini"] = None
        else:
            from google import genai
            _client_state["gemini"] = genai.Client(api_key=api_key)
    return _client_state["gemini"]


def _to_gemini_schema(schema):
    """Gemini's FunctionDeclaration.parameters is an OpenAPI-style Schema —
    it has no JSON-Schema-style `"type": ["string", "null"]` union; nullable
    fields are `type: <that type>` + `nullable: true` instead. Recurses so
    callers' plain JSON-Schema dicts (shared with the Anthropic/Ollama path)
    never need to know this."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, list):
            non_null = [t for t in value if t != "null"]
            out["type"] = non_null[0] if non_null else "string"
            if "null" in value:
                out["nullable"] = True
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


def _call_gemini(system_prompt: str, user_message: str, tool_name: str, tool_schema: dict) -> dict | None:
    client = _get_gemini_client()
    if client is None:
        print("[chat] GEMINI_API_KEY not set — LLM stage unavailable, falling back", file=sys.stderr)
        return None

    from google.genai import types

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=MAX_TOKENS,
                tools=[types.Tool(function_declarations=[types.FunctionDeclaration(
                    name=tool_name,
                    description=tool_schema.get("description", ""),
                    parameters=_to_gemini_schema(tool_schema["input_schema"]),
                )])],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=[tool_name],
                    )
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — any SDK/network failure degrades gracefully
        print(f"[chat] Gemini call failed ({tool_name}): {exc}", file=sys.stderr)
        return None

    for candidate in response.candidates or []:
        for part in candidate.content.parts or []:
            fc = getattr(part, "function_call", None)
            if fc and fc.name == tool_name:
                return dict(fc.args)

    print(f"[chat] Gemini response had no {tool_name} function call", file=sys.stderr)
    return None


# --- Ollama (local) --------------------------------------------------------

def _get_ollama_client():
    if "ollama" not in _client_state:
        import ollama
        _client_state["ollama"] = ollama.Client(host=OLLAMA_HOST)
    return _client_state["ollama"]


def _call_ollama(system_prompt: str, user_message: str, tool_name: str, tool_schema: dict) -> dict | None:
    try:
        client = _get_ollama_client()
    except Exception as exc:  # noqa: BLE001 — e.g. the ollama package failing to import
        print(f"[chat] Ollama client unavailable: {exc}", file=sys.stderr)
        return None

    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_schema.get("description", ""),
            "parameters": tool_schema["input_schema"],
        },
    }]

    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=tools,
        )
    except Exception as exc:  # noqa: BLE001 — host unreachable, model not pulled, etc.
        print(f"[chat] Ollama call failed ({tool_name}) against {OLLAMA_HOST} model={OLLAMA_MODEL}: {exc}", file=sys.stderr)
        return None

    tool_calls = (response.get("message") or {}).get("tool_calls") or []
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        if fn.get("name") == tool_name:
            args = fn.get("arguments")
            return dict(args) if args else {}

    print(f"[chat] Ollama response had no {tool_name} tool call — model may not support "
          f"tool calling, or didn't choose to use it", file=sys.stderr)
    return None


# --- Dispatch ----------------------------------------------------------

def call_tool(*, system_prompt: str, user_message: str, tool_name: str, tool_schema: dict) -> dict | None:
    """Forces the model to respond via exactly one tool call and returns its
    validated `input`/`arguments` dict, or None if the LLM is
    unavailable/unusable for this call (never raises). Provider is chosen by
    AGENT_LLM_PROVIDER — callers don't need to know or care which is active."""
    if PROVIDER == "ollama":
        return _call_ollama(system_prompt, user_message, tool_name, tool_schema)
    if PROVIDER == "gemini":
        return _call_gemini(system_prompt, user_message, tool_name, tool_schema)
    if PROVIDER != "anthropic":
        print(f"[chat] unknown AGENT_LLM_PROVIDER={PROVIDER!r}, defaulting to anthropic", file=sys.stderr)
    return _call_anthropic(system_prompt, user_message, tool_name, tool_schema)
