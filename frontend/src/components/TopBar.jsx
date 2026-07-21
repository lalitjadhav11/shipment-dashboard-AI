export default function TopBar() {
  return (
    <header className="topbar">
      <div className="topbar__logo" aria-label="Shipment Dashboard">
        <span className="topbar__logo-ship">Ship</span>
        <span className="topbar__logo-track">Track</span>
      </div>

      <div className="topbar__spacer" />

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
