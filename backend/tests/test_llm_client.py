"""
Circuit breaker unit tests — fully offline (no real provider call, no
sleeping: time.monotonic is faked so cooldown behavior is deterministic and
instant). Each test resets llm_client's module-level circuit state first,
since it's process-global by design (shared across every call_tool()
invocation, the same way a real circuit breaker would be).
"""
import threading

import pytest

from chat import llm_client


@pytest.fixture(autouse=True)
def _reset_circuit(monkeypatch):
    monkeypatch.setattr(llm_client, "_circuit_state", {})
    monkeypatch.setattr(llm_client, "PROVIDER", "anthropic")


def test_circuit_opens_after_threshold_consecutive_failures(monkeypatch):
    monkeypatch.setattr(llm_client, "CIRCUIT_FAILURE_THRESHOLD", 2)
    calls = {"n": 0}

    def failing_call(*args, **kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr(llm_client, "_call_anthropic", failing_call)

    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert calls["n"] == 2  # both real attempts made — threshold not yet exceeded during them

    # Third call: circuit should now be open — fails fast, no new attempt made.
    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert calls["n"] == 2


def test_successful_call_resets_the_failure_count(monkeypatch):
    monkeypatch.setattr(llm_client, "CIRCUIT_FAILURE_THRESHOLD", 2)
    responses = iter([None, {"ok": True}, None, None])
    monkeypatch.setattr(llm_client, "_call_anthropic", lambda *a, **k: next(responses))

    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) == {"ok": True}
    # Success reset consecutive_failures to 0, so it takes a FULL new streak
    # of 2 failures to trip the breaker again, not just one more.
    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert llm_client._circuit_state["anthropic"]["opened_at"] is None


def test_circuit_half_opens_after_cooldown_elapses(monkeypatch):
    fake_time = {"t": 1000.0}
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: fake_time["t"])
    monkeypatch.setattr(llm_client, "CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(llm_client, "CIRCUIT_COOLDOWN_SECONDS", 60)

    responses = iter([None, {"ok": True}])
    calls = {"n": 0}

    def fake_call(*args, **kwargs):
        calls["n"] += 1
        return next(responses)

    monkeypatch.setattr(llm_client, "_call_anthropic", fake_call)

    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert calls["n"] == 1  # trips the breaker (threshold=1)

    # Still within cooldown — fails fast, no new attempt.
    fake_time["t"] += 30
    assert llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={}) is None
    assert calls["n"] == 1

    # Cooldown fully elapsed — one real trial attempt is let through.
    fake_time["t"] += 31
    result = llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={})
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_no_usable_tool_call_counts_as_a_failure_for_circuit_purposes(monkeypatch):
    # The documented Ollama gap this breaker targets: the provider responds
    # without ever calling the tool — not an exception, just an unusable
    # response — and call_tool() already turns that into None. The circuit
    # must treat that the same as a hard error.
    monkeypatch.setattr(llm_client, "CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(llm_client, "_call_anthropic", lambda *a, **k: None)

    llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={})
    assert llm_client._circuit_state["anthropic"]["opened_at"] is not None


def test_different_providers_have_independent_circuits(monkeypatch):
    monkeypatch.setattr(llm_client, "CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(llm_client, "_call_anthropic", lambda *a, **k: None)
    monkeypatch.setattr(llm_client, "_call_gemini", lambda *a, **k: {"ok": True})

    monkeypatch.setattr(llm_client, "PROVIDER", "anthropic")
    llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={})

    monkeypatch.setattr(llm_client, "PROVIDER", "gemini")
    result = llm_client.call_tool(system_prompt="s", user_message="u", tool_name="t", tool_schema={})
    assert result == {"ok": True}  # gemini's circuit is unaffected by anthropic's failure


def test_concurrent_failures_are_not_undercounted(monkeypatch):
    # Regression for a race found while auditing: FastAPI runs sync route
    # handlers (chat(), plus main.py's ai-summary/ask endpoints) in a worker
    # thread pool, so concurrent requests hitting the same provider genuinely
    # call _record_failure() from different threads at once. Without
    # _circuit_lock, "consecutive_failures += 1" (read-modify-write, not
    # atomic) can lose increments under real concurrency. High iteration
    # count + a barrier-style start (all threads launched before any join)
    # maximizes the chance of exposing the race if the lock were removed.
    monkeypatch.setattr(llm_client, "CIRCUIT_FAILURE_THRESHOLD", 10**9)  # never opens mid-test
    n_threads = 20
    calls_per_thread = 50

    def hammer():
        for _ in range(calls_per_thread):
            llm_client._record_failure("anthropic")

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert llm_client._circuit_state["anthropic"]["consecutive_failures"] == n_threads * calls_per_thread
