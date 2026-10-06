import { useEffect, useState } from "react";
import { notebooksApi } from "../services/notebooksService";
import { ApiError } from "../types/auth";
import type { Document } from "../types/documents";
import type { NotebookOverview, NotebookOverviewCitation } from "../types/knowledge";
import { Markdown } from "./Markdown";

// Lightweight variant of CitationPanel.tsx's source-span viewer, adapted for the
// section-level citation shape the P1 Notebook Overview map-reduce path produces
// (char_start/char_end/chunk_id are always null here — a synthesized section-level
// answer has no single chunk span to point at; heading/section_id are populated
// instead). Not a straight reuse of CitationPanel: that component's props are typed
// to the chunk-path's ALWAYS-populated char_start/char_end, so it would need its own
// nullable-prop changes to accept this shape — reusing only its visual language
// (the `border-l-2 border-accent` quote style) here instead.
function OverviewCitationPanel({
  citation,
  documents,
  onClose,
}: {
  citation: NotebookOverviewCitation;
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
        {citation.heading && <p className="text-xs text-muted">Section: {citation.heading}</p>}
        <blockquote className="border-l-2 border-accent pl-3 text-sm text-text whitespace-pre-wrap leading-relaxed">
          {citation.content}
        </blockquote>
      </div>
    </div>
  );
}

// Renders the overview's synthesized text as markdown (D5) with [n] markers as
// clickable citation buttons — same convention as ChatPanel.tsx's AnswerText, adapted
// for NotebookOverviewCitation.
function OverviewText({
  content,
  citations,
  onCitationClick,
}: {
  content: string;
  citations: NotebookOverviewCitation[];
  onCitationClick: (c: NotebookOverviewCitation) => void;
}) {
  return (
    <Markdown
      content={content}
      renderCitation={(marker, raw, key) => {
        const citation = citations.find((c) => c.marker === marker);
        return citation ? (
          <button
            key={key}
            type="button"
            onClick={() => onCitationClick(citation)}
            className="font-mono text-accent text-xs hover:underline align-super px-0.5"
          >
            [{marker}]
          </button>
        ) : (
          <span key={key} className="font-mono text-muted text-xs">
            {raw}
          </span>
        );
      }}
    />
  );
}

type LoadState = "loading" | "empty" | "ready" | "error";

export function NotebookOverviewPanel({
  notebookId,
  documents,
}: {
  notebookId: string;
  documents: Document[];
}) {
  const [state, setState] = useState<LoadState>("loading");
  const [overview, setOverview] = useState<NotebookOverview | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [activeCitation, setActiveCitation] = useState<NotebookOverviewCitation | null>(null);

  // Load the cached overview (if any) on mount and whenever the notebook changes.
  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setOverview(null);
    setErrorMessage(null);
    setActiveCitation(null);
    notebooksApi
      .getOverview(notebookId)
      .then((data) => {
        if (cancelled) return;
        setOverview(data);
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState("empty");
        } else {
          setState("error");
          setErrorMessage(err instanceof ApiError ? err.message : "Failed to load overview.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [notebookId]);

  function handleGenerate() {
    setIsGenerating(true);
    setErrorMessage(null);
    notebooksApi
      .generateOverview(notebookId)
      .then((data) => {
        setOverview(data);
        setState("ready");
        setActiveCitation(null);
      })
      .catch((err) => {
        setErrorMessage(
          err instanceof ApiError ? err.message : "Failed to generate the overview.",
        );
      })
      .finally(() => setIsGenerating(false));
  }

  const showCitationPanel = activeCitation !== null;
  const buttonLabel = isGenerating ? "Generating…" : overview ? "Regenerate" : "Generate Overview";

  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex flex-col flex-1 min-h-0">
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {state === "loading" && <p className="text-muted text-sm">Loading…</p>}

          {state === "empty" && (
            <div className="text-center mt-8 space-y-3">
              <p className="text-muted text-sm">
                No overview yet. Generate a summary of everything covered across this
                notebook's documents.
              </p>
            </div>
          )}

          {overview?.stale && (
            <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
              This overview may be out of date — documents in this notebook have changed
              since it was generated. Regenerate to refresh it.
            </div>
          )}

          {errorMessage && (
            <div className="rounded-md border border-danger/50 bg-danger/10 px-3 py-2 text-xs text-danger">
              {errorMessage}
            </div>
          )}

          {overview && (
            <div className="bg-surface border border-border rounded-lg px-4 py-3 text-sm text-text">
              <OverviewText
                content={overview.content}
                citations={overview.citations}
                onCitationClick={setActiveCitation}
              />
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border p-4">
          <button
            type="button"
            onClick={handleGenerate}
            disabled={isGenerating}
            className="text-sm bg-accent text-white rounded-md px-4 py-2 hover:opacity-90 disabled:opacity-50"
          >
            {buttonLabel}
          </button>
        </div>
      </div>

      {showCitationPanel && activeCitation && (
        <div className="fixed inset-0 z-40 bg-bg lg:static lg:inset-auto lg:z-auto lg:w-80 shrink-0 border-l border-border flex flex-col overflow-hidden">
          <OverviewCitationPanel
            citation={activeCitation}
            documents={documents}
            onClose={() => setActiveCitation(null)}
          />
        </div>
      )}
    </div>
  );
}
