import { useState } from "react";
import { SearchIcon } from "./icons.jsx";

export default function TopBar({ value = "", onAsk }) {
  const [input, setInput] = useState(value);

  function handleSubmit(event) {
    event.preventDefault();
    const trimmed = input.trim();
    if (trimmed) onAsk?.(trimmed);
  }

  return (
    <header className="topbar">
      <div className="topbar__logo" aria-label="Shipment Dashboard">
        <span className="topbar__logo-ship">Ship</span>
        <span className="topbar__logo-track">Track</span>
      </div>

      <form className="topbar__search" onSubmit={handleSubmit}>
        <input
          type="text"
          placeholder="Ask the AI assistant about your shipments…"
          aria-label="Ask the AI assistant"
          value={input}
          onChange={(event) => setInput(event.target.value)}
        />
        <button type="submit" aria-label="Ask">
          <SearchIcon width={16} height={16} />
        </button>
      </form>

      <div className="topbar__account">
        <span className="topbar__avatar">C</span>
        <span className="topbar__account-name">COC</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
    </header>
  );
}
