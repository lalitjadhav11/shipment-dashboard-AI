import { SparkleIcon } from "./icons.jsx";

const EXAMPLE_PROMPTS = [
  "Where is my package 400000000154?",
  "Why is my shipment delayed?",
  "How many international shipments are held in customs?",
];

export default function AiInsightPanel({ query, answer, loading, error, onAsk }) {
  return (
    <section className="ai-panel" aria-label="AI shipment assistant">
      <div className="ai-panel__header">
        <span className="ai-panel__badge">
          <SparkleIcon width={16} height={16} />
        </span>
        <div>
          <div className="ai-panel__title">AI Shipment Assistant</div>
          <div className="ai-panel__subtitle">Ask a question in the search bar above</div>
        </div>
      </div>

      <div className="ai-panel__body">
        {!query && !loading && !error && (
          <div className="ai-panel__placeholder">
            <p>Try asking things like:</p>
            <div className="ai-panel__suggestions">
              {EXAMPLE_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="ai-panel__chip"
                  onClick={() => onAsk?.(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {query && (
          <div className="ai-panel__query">
            <span className="ai-panel__query-label">You asked</span>
            <span className="ai-panel__query-text">&ldquo;{query}&rdquo;</span>
          </div>
        )}

        {loading && (
          <div className="ai-panel__loading">
            <span className="ai-panel__loading-dot" />
            <span className="ai-panel__loading-dot" />
            <span className="ai-panel__loading-dot" />
            <span>Thinking through your shipment data…</span>
          </div>
        )}

        {!loading && error && <div className="ai-panel__error">{error}</div>}

        {!loading && !error && answer && (
          <div className="ai-panel__answer-block">
            <p className="ai-panel__answer">{answer.answer}</p>

            {(answer.tracking_id || answer.current_status) && (
              <div className="ai-panel__meta">
                {answer.tracking_id && (
                  <span className="ai-panel__meta-tag">
                    Tracking ID <strong>{answer.tracking_id}</strong>
                  </span>
                )}
                {answer.current_status && (
                  <span className="ai-panel__meta-tag">
                    Status <strong>{answer.current_status.replaceAll("_", " ")}</strong>
                  </span>
                )}
              </div>
            )}

            {answer.follow_up_suggestions?.length > 0 && (
              <div className="ai-panel__suggestions">
                {answer.follow_up_suggestions.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="ai-panel__chip"
                    onClick={() => onAsk?.(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
