import TopBar from "./components/TopBar.jsx";
import AiChatPanel from "./components/AiChatPanel.jsx";
import ShipmentsTable from "./components/ShipmentsTable.jsx";

export default function App() {
  return (
    <div className="app-shell">
      <TopBar />
      <div className="app-body">
        <main className="app-main">
          <div className="page-header">
            <div>
              <h1>Shipment reporting</h1>
              <p>Real-time visibility into every shipment across your account</p>
            </div>
          </div>

          <AiChatPanel />
          <ShipmentsTable />
        </main>
      </div>
    </div>
  );
}
