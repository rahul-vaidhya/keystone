import type { ResolvedCitation } from "../types/chat";
import type { Document } from "../types/documents";

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

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <span className="text-sm font-medium">Source [{citation.marker}]</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close citation panel"
          className="text-muted hover:text-text transition p-1 rounded"
        >
          ×
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {doc && (
          <p className="text-xs text-muted truncate" title={doc.title}>
            {doc.title}
          </p>
        )}
        <p className="font-mono text-xs text-muted">
          chars {citation.char_start}–{citation.char_end}
        </p>
        <blockquote className="border-l-2 border-accent pl-3 text-sm text-text whitespace-pre-wrap leading-relaxed">
          {citation.content}
        </blockquote>
      </div>
    </div>
  );
}
