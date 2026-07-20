// Minimal inline SVG icon set — outline style, currentColor stroke,
// so no external icon library / network fetch is needed at build time.

const base = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export const SearchIcon = (props) => (
  <svg {...base} {...props}>
    <circle cx="11" cy="11" r="7" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
);

export const FilterIcon = (props) => (
  <svg {...base} {...props}>
    <polygon points="4 4 20 4 14 12.5 14 19 10 21 10 12.5 4 4" />
  </svg>
);

export const ChevronDown = (props) => (
  <svg {...base} {...props}>
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

export const ChevronLeft = (props) => (
  <svg {...base} {...props}>
    <polyline points="15 18 9 12 15 6" />
  </svg>
);

export const ChevronRight = (props) => (
  <svg {...base} {...props}>
    <polyline points="9 18 15 12 9 6" />
  </svg>
);

export const SortArrow = ({ direction = "desc", ...props }) => (
  <svg {...base} width={14} height={14} {...props}>
    {direction === "asc" ? (
      <polyline points="18 15 12 9 6 15" />
    ) : (
      <polyline points="6 9 12 15 18 9" />
    )}
  </svg>
);

export const ExternalLinkIcon = (props) => (
  <svg {...base} width={14} height={14} {...props}>
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </svg>
);

export const BoxIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M21 8 12 3 3 8v8l9 5 9-5V8Z" />
    <path d="M3 8l9 5 9-5" />
    <line x1="12" y1="13" x2="12" y2="21" />
  </svg>
);

export const QuoteIcon = (props) => (
  <svg {...base} {...props}>
    <rect x="4" y="3" width="16" height="18" rx="2" />
    <line x1="8" y1="8" x2="16" y2="8" />
    <line x1="8" y1="12" x2="16" y2="12" />
    <line x1="8" y1="16" x2="12" y2="16" />
  </svg>
);

export const TruckIcon = (props) => (
  <svg {...base} {...props}>
    <rect x="1" y="7" width="13" height="10" rx="1" />
    <path d="M14 10h4l3 3v4h-7z" />
    <circle cx="6" cy="18.5" r="1.5" />
    <circle cx="17" cy="18.5" r="1.5" />
  </svg>
);

export const BillingIcon = (props) => (
  <svg {...base} {...props}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <line x1="7" y1="9" x2="17" y2="9" />
    <line x1="7" y1="13" x2="17" y2="13" />
    <line x1="7" y1="17" x2="12" y2="17" />
  </svg>
);

export const ReportingIcon = (props) => (
  <svg {...base} {...props}>
    <line x1="5" y1="20" x2="5" y2="12" />
    <line x1="12" y1="20" x2="12" y2="7" />
    <line x1="19" y1="20" x2="19" y2="15" />
    <line x1="3" y1="20" x2="21" y2="20" />
  </svg>
);

export const ClaimsIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M12 2 4 5v6c0 5 3.4 8.4 8 11 4.6-2.6 8-6 8-11V5Z" />
    <path d="M9 12l2 2 4-4" />
  </svg>
);

export const SupportIcon = (props) => (
  <svg {...base} {...props}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 15a3 3 0 1 0-3-3" />
    <line x1="12" y1="17.5" x2="12" y2="17.51" />
  </svg>
);

export const SuppliesIcon = (props) => (
  <svg {...base} {...props}>
    <rect x="3" y="3" width="8" height="8" rx="1" />
    <rect x="13" y="3" width="8" height="8" rx="1" />
    <rect x="3" y="13" width="8" height="8" rx="1" />
    <rect x="13" y="13" width="8" height="8" rx="1" />
  </svg>
);

export const LocationIcon = (props) => (
  <svg {...base} {...props}>
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10" r="3" />
  </svg>
);

export const DeveloperIcon = (props) => (
  <svg {...base} {...props}>
    <polyline points="8 6 2 12 8 18" />
    <polyline points="16 6 22 12 16 18" />
  </svg>
);

export const CloseIcon = (props) => (
  <svg {...base} {...props}>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);

export const PinIcon = (props) => (
  <svg {...base} width={14} height={14} {...props}>
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10" r="3" />
  </svg>
);

// Filled 4-point sparkle/diamond — the AI Shipment Assistant's brand mark.
// Breaks from the outline `base` style deliberately: sparkle glyphs read
// better solid than stroked.
export const SparkleIcon = (props) => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="currentColor" {...props}>
    <path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2Z" />
  </svg>
);

export const UploadIcon = (props) => (
  <svg {...base} width={14} height={14} {...props}>
    <path d="M12 19V5" />
    <polyline points="5 12 12 5 19 12" />
    <line x1="4" y1="21" x2="20" y2="21" />
  </svg>
);
