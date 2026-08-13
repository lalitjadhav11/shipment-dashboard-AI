import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";

// The AI's answer text is plain natural language by contract (see
// synthesize.py's system prompt — "natural-language answer", nothing about
// markdown), but models routinely reach for markdown-style emphasis on
// their own anyway: **bold** stats in a breakdown, "- " bulleted lists,
// occasional inline `code`-style values. Rendering that as literal text
// showed the raw asterisks/dashes instead of the emphasis the model
// actually intended. remark-breaks turns a single newline into a real line
// break — live-observed models often separate a run of stats with one \n
// each and no blank line between them, which plain CommonMark treats as a
// soft break (rendered as a space, everything runs together on one line)
// rather than the visually separated list the model clearly intended. No
// rehype-raw plugin is used, so any raw HTML that happened to be in the
// source is escaped as text rather than executed — safe by default for
// LLM-generated content, no sanitizer needed on top.
const ALLOWED_ELEMENTS = [
  "p", "strong", "em", "del", "ul", "ol", "li", "code", "pre", "br", "a", "blockquote",
  "table", "thead", "tbody", "tr", "th", "td",
];

function ExternalLink({ href, children }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/** Renders an AI answer's markdown-ish formatting safely. `className` is
 * applied to the wrapping element for the existing font-size/color
 * context; block-level spacing for the rendered elements themselves lives
 * in index.css, scoped under that same class. */
export default function FormattedAnswer({ text, className }) {
  if (!text) return null;
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        allowedElements={ALLOWED_ELEMENTS}
        unwrapDisallowed
        components={{ a: ExternalLink }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
