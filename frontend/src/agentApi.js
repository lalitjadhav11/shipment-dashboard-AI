// Service module for the AI Shipment Journey Summary chat (see
// AGENTIC_RAG_ARCHITECTURE.md). Kept separate from api.js — the dashboard's
// plain-JSON REST calls — because this one talks to a POST + Server-Sent
// Events endpoint and any component may want to ask the agent a question,
// not just the top search bar.
import { API_BASE } from "./api.js";

/**
 * Calls POST /api/chat and returns the final answer payload.
 * SSE over POST can't use EventSource (GET-only), so this reads the response
 * body as a stream and parses `data: {...}\n\n` frames by hand.
 * Deliberately omits the X-User-Role header: the verbose per-stage trace is a
 * SUPPORT/OPS/ADMIN privilege (see backend/chat/router.py), and this is the
 * customer-facing dashboard, so only the final `answer_ready` event is ever
 * streamed back here anyway.
 */
export async function askAgent(query, { signal } = {}) {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`/api/chat -> HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastEvent = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? ""; // last chunk may be a partial frame — keep for next read
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      try {
        lastEvent = JSON.parse(line.slice(5).trim());
      } catch {
        // ignore a malformed/partial frame rather than aborting the whole answer
      }
    }
  }

  if (!lastEvent?.detail) {
    throw new Error("The AI agent didn't return a response");
  }
  return lastEvent.detail;
}
