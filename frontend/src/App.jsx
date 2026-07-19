import { useEffect, useState } from "react";
import TopBar from "./components/TopBar.jsx";
import SideBar from "./components/SideBar.jsx";
import KpiRow from "./components/KpiRow.jsx";
import ShipmentsTable from "./components/ShipmentsTable.jsx";
import { fetchSummary } from "./api.js";

export default function App() {
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  useEffect(() => {
    fetchSummary()
      .then(setSummary)
      .finally(() => setSummaryLoading(false));
  }, []);

  return (
    <div className="app-shell">
      <TopBar />
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
          <ShipmentsTable />
        </main>
      </div>
    </div>
  );
}
