import { useEffect, useState } from "react";

// The nginx image proxies /api to the backend container (see nginx.conf),
// so a relative path works both in the browser and inside Docker.
const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export default function App() {
  const [backend, setBackend] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/hello`)
      .then((r) => r.json())
      .then((data) => setBackend(data.message))
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="card">
      <h1>Hello World 👋</h1>
      <p>Shipment Dashboard — Phase 1</p>
      <p>React frontend is up and running.</p>

      {backend && <div className="status ok">backend says: {backend}</div>}
      {error && <div className="status err">backend not reachable yet…</div>}
    </div>
  );
}
