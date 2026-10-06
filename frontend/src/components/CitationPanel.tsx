import type { ResolvedCitation } from "../types/chat";
import type { Document } from "../types/documents";

// The parser emits markdown: a "### Page N" line per PDF page plus "#"-style headings.
// The page is already shown above the quote, so page-marker lines are dropped and other
// heading markers are stripped so the snippet reads as plain text. Char offsets live in
// the admin Debug panel only.
const PAGE_MARKER_LINE = /^#{1,6}\s*Page\s+\d+\s*$/;
const HEADING_PREFIX = /^#{1,6}\s+/;

function cleanSnippet(content: string): string {
  return content
    .split("\n")
    .filter((line) => !PAGE_MARKER_LINE.test(line.trim()))
    .map((line) => line.replace(HEADING_PREFIX, ""))
    .join("\n")
    .trim();
}

export function CitationPanel({
  citation,
  documents,
  onClose,
}: {
  citation: ResolvedCitation;
  documents: Document[];
  onClose: () => void;
}) {
  const doc = documents.find((d) => d.id === citation.document_id);
  const isSection = citation.citation_type === "section";
  // Page info is chunk-path only and null when unknown (the backend also nulls a
  // whole-document range, which says nothing about where the text is) — hide it then.
  const hasPageInfo = !isSection && citation.page_start !== null;
  const pageLabel =
    citation.page_end === null || citation.page_end === citation.page_start
      ? `Page ${citation.page_start}`
      : `Pages ${citation.page_start}–${citation.page_end}`;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <span className="text-sm font-medium">Source [{citation.marker}]</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close citation panel"
          className="text-lg leading-none text-muted hover:text-text hover:bg-surface transition px-2 py-1 rounded-md"
        >
          ×
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {doc && (
          <p className="text-sm font-medium text-text truncate" title={doc.title}>
            {doc.title}
          </p>
        )}
        {hasPageInfo && <p className="text-xs text-muted">{pageLabel}</p>}
        {isSection && citation.heading && (
          <p className="text-xs text-muted">Section: {citation.heading}</p>
        )}
        <blockquote className="border-l-2 border-accent pl-3 text-sm text-text whitespace-pre-wrap leading-relaxed">
          {cleanSnippet(citation.content)}
        </blockquote>
      </div>
    </div>
  );
}
