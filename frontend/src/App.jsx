import { useCallback, useEffect, useRef, useState } from "react";
import TopBar from "./components/TopBar.jsx";
import SideBar from "./components/SideBar.jsx";
import KpiRow from "./components/KpiRow.jsx";
import AiInsightPanel from "./components/AiInsightPanel.jsx";
import ShipmentsTable from "./components/ShipmentsTable.jsx";
import { fetchSummary } from "./api.js";
import { askAgent } from "./agentApi.js";

export default function App() {
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  const [agentQuery, setAgentQuery] = useState("");
  const [agentAnswer, setAgentAnswer] = useState(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentError, setAgentError] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    fetchSummary()
      .then(setSummary)
      .finally(() => setSummaryLoading(false));
  }, []);

  const handleAsk = useCallback((query) => {
    abortRef.current?.abort(); // a new question supersedes any in-flight one
    const controller = new AbortController();
    abortRef.current = controller;

    setAgentQuery(query);
    setAgentAnswer(null);
    setAgentError(null);
    setAgentLoading(true);

    askAgent(query, { signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) return;
        setAgentAnswer(detail);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setAgentError(err.message || "Something went wrong asking the AI assistant.");
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setAgentLoading(false);
      });
  }, []);

  return (
    <div className="app-shell">
      <TopBar value={agentQuery} onAsk={handleAsk} />
      <div className="app-body">
        <SideBar />
        <main className="app-main">
          <div className="page-header">
            <div>
              <h1>Shipment reporting</h1>
              <p>Real-time visibility into every shipment across your account</p>
            </div>
          </div>

          <KpiRow summary={summary} loading={summaryLoading} />
          <AiInsightPanel
            query={agentQuery}
            answer={agentAnswer}
            loading={agentLoading}
            error={agentError}
            onAsk={handleAsk}
          />
          <ShipmentsTable />
        </main>
      </div>
    </div>
  );
}
