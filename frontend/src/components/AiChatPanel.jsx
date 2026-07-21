import { useCallback, useRef, useState } from "react";
import { askAgent } from "../agentApi.js";
import { SparkleIcon } from "./icons.jsx";

// Self-contained: owns the composer input AND the conversation history, so
// the "AI Shipment Assistant" card only exists once there's something to
// show — before the first question, this renders just the composer row.
export default function AiChatPanel() {
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState([]); // [{ id, query, loading, answer, error }]
  const abortRef = useRef(null);
  const nextIdRef = useRef(0);

  const busy = turns.some((t) => t.loading);
  const hasConversation = turns.length > 0;

  const ask = useCallback((rawQuery) => {
    const query = rawQuery.trim();
    if (!query) return;

    abortRef.current?.abort(); // a new question supersedes any in-flight one
    const controller = new AbortController();
    abortRef.current = controller;

    const id = ++nextIdRef.current;
    setTurns((prev) => [...prev, { id, query, loading: true, answer: null, error: null }]);

    askAgent(query, { signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) return;
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, loading: false, answer: detail } : t)));
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setTurns((prev) => prev.map((t) => (
          t.id === id ? { ...t, loading: false, error: err.message || "Something went wrong asking the AI assistant." } : t
        )));
      });
  }, []);

  function handleSubmit(event) {
    event.preventDefault();
    ask(input);
    setInput("");
  }

  function clearChat() {
    abortRef.current?.abort();
    setTurns([]);
  }

  return (
    <section className="ai-chat" aria-label="AI shipment assistant">
      <form className="ai-chat__composer" onSubmit={handleSubmit}>
        <SparkleIcon className="ai-chat__composer-icon" width={16} height={16} />
        <input
          type="text"
          placeholder="Ask the AI assistant about your shipments…"
          aria-label="Ask the AI assistant"
          value={input}
          onChange={(event) => setInput(event.target.value)}
        />
        <button type="submit" className="ai-chat__ask-btn" disabled={busy || !input.trim()}>
          Ask AI
        </button>
      </form>

      {hasConversation && (
        <div className="ai-chat__conversation">
          <div className="ai-chat__conversation-header">
            <button type="button" className="ai-chat__clear-btn" onClick={clearChat}>
              Clear chat
            </button>
          </div>

          {turns.map((turn) => (
            <div className="ai-chat__turn" key={turn.id}>
              <div className="ai-chat__question">
                <span className="ai-chat__question-label">You asked</span>
                <span className="ai-chat__question-text">&ldquo;{turn.query}&rdquo;</span>
              </div>

              {turn.loading && (
                <div className="ai-chat__loading">
                  <span className="ai-chat__loading-dot" />
                  <span className="ai-chat__loading-dot" />
                  <span className="ai-chat__loading-dot" />
                  <span>Thinking through your shipment data…</span>
                </div>
              )}

              {!turn.loading && turn.error && <div className="ai-chat__error">{turn.error}</div>}

              {!turn.loading && !turn.error && turn.answer && (
                <div className="ai-chat__answer-row">
                  <span className="ai-chat__answer-badge">
                    <SparkleIcon width={13} height={13} />
                  </span>
                  <div className="ai-chat__answer-block">
                    <p className="ai-chat__answer">{turn.answer.answer}</p>

                    {(turn.answer.tracking_id || turn.answer.current_status) && (
                      <div className="ai-chat__meta">
                        {turn.answer.tracking_id && (
                          <span className="ai-chat__meta-tag">
                            Tracking ID <strong>{turn.answer.tracking_id}</strong>
                          </span>
                        )}
                        {turn.answer.current_status && (
                          <span className="ai-chat__meta-tag">
                            Status <strong>{turn.answer.current_status.replaceAll("_", " ")}</strong>
                          </span>
                        )}
                      </div>
                    )}

                    {turn.answer.follow_up_suggestions?.length > 0 && (
                      <div className="ai-chat__suggestions">
                        {turn.answer.follow_up_suggestions.map((prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            className="ai-chat__chip"
                            onClick={() => ask(prompt)}
                            disabled={busy}
                          >
                            {prompt}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
