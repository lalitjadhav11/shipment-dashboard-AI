import {
  BoxIcon,
  QuoteIcon,
  TruckIcon,
  BillingIcon,
  ReportingIcon,
  ClaimsIcon,
  ChevronDown,
  ExternalLinkIcon,
} from "./icons.jsx";

function NavItem({ icon, label, expandable }) {
  return (
    <button type="button" className="sidenav__item">
      {icon}
      <span>{label}</span>
      {expandable && <ChevronDown className="sidenav__chevron" width={14} height={14} />}
    </button>
  );
}

function ExternalItem({ label, icon }) {
  return (
    <a className="sidenav__ext" href="#" onClick={(e) => e.preventDefault()}>
      {icon}
      <span>{label}</span>
      <ExternalLinkIcon className="sidenav__ext-icon" />
    </a>
  );
}

export default function SideBar() {
  return (
    <nav className="sidenav" aria-label="Primary">
      <NavItem icon={<BoxIcon />} label="Ship" expandable />
      <NavItem icon={<QuoteIcon />} label="Rates & quotes" expandable />
      <NavItem icon={<TruckIcon />} label="Pickups" expandable />

      <div className="sidenav__section">Account management</div>
      <ExternalItem label="Billing & payments" icon={<BillingIcon />} />
      <ExternalItem label="Reporting" icon={<ReportingIcon />} />
      <ExternalItem label="Claims" icon={<ClaimsIcon />} />

      <div className="sidenav__section">Help & resources</div>
      <ExternalItem label="Support" />
      <ExternalItem label="Supplies" />
      <ExternalItem label="Find a location" />
      <ExternalItem label="Developer portal" />
    </nav>
  );
}
