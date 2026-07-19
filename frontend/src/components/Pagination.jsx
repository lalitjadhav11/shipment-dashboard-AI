import { ChevronLeft, ChevronRight } from "./icons.jsx";

function buildPageList(current, totalPages) {
  const pages = new Set([1, totalPages, current, current - 1, current + 1]);
  return [...pages]
    .filter((p) => p >= 1 && p <= totalPages)
    .sort((a, b) => a - b);
}

export default function Pagination({ page, totalPages, onChange }) {
  if (totalPages <= 1) return null;

  const pages = buildPageList(page, totalPages);
  let lastRendered = 0;

  return (
    <nav className="pagination" aria-label="Table pagination">
      <button
        type="button"
        className="pagination__nav"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        aria-label="Previous page"
      >
        <ChevronLeft width={16} height={16} />
      </button>

      {pages.map((p) => {
        const showGap = p - lastRendered > 1;
        lastRendered = p;
        return (
          <span key={p} style={{ display: "contents" }}>
            {showGap && <span className="pagination__ellipsis">…</span>}
            <button
              type="button"
              className={`pagination__page ${p === page ? "pagination__page--active" : ""}`}
              onClick={() => onChange(p)}
              aria-current={p === page ? "page" : undefined}
            >
              {p}
            </button>
          </span>
        );
      })}

      <button
        type="button"
        className="pagination__nav"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        aria-label="Next page"
      >
        <ChevronRight width={16} height={16} />
      </button>
    </nav>
  );
}
