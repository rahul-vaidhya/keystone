import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { notebooksApi } from "../services/notebooksService";
import { documentsApi } from "../services/documentsService";
import { retrievalApi } from "../services/retrievalService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";
import type { ContextBlock } from "../types/retrieval";
import type { ResolvedCitation } from "../types/chat";
import { CitationPanel } from "../components/CitationPanel";

export function SearchPage() {
  const dialog = useDialog();
  const [notebookId, setNotebookId] = useState("");
  const [query, setQuery] = useState("");
  const [activeCitation, setActiveCitation] = useState<ResolvedCitation | null>(null);

  const notebooksQuery = useQuery({ queryKey: ["notebooks"], queryFn: notebooksApi.list });

  const allDocsQuery = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.listDocuments(),
  });

  const searchMutation = useMutation({
    mutationFn: () =>
      retrievalApi.search({ notebook_id: notebookId, query: query.trim(), k: 8 }),
    onError: (err) =>
      void dialog.alert(err instanceof ApiError ? err.message : "Search failed. Please try again."),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!notebookId || !query.trim()) return;
    setActiveCitation(null);
    searchMutation.mutate();
  }

  function resolveTitle(documentId: string): string {
    const doc = allDocsQuery.data?.find((d) => d.id === documentId);
    return doc?.title ?? `Document ${documentId.slice(0, 8)}…`;
  }

  function openCitation(hit: ContextBlock) {
    setActiveCitation({
      marker: hit.index,
      document_id: hit.document_id,
      chunk_id: hit.chunk_id,
      char_start: hit.char_start,
      char_end: hit.char_end,
      content: hit.content,
      // /retrieval/search's ContextBlock doesn't carry page info (that's a chat-citation
      // only field, resolved server-side from the chunk's section) — never fabricate it.
      page_start: null,
      page_end: null,
    });
  }

  const notebooks = notebooksQuery.data ?? [];
  const hasNotebooks = notebooks.length > 0;
  const results = searchMutation.data?.results ?? [];
  const hasSearched = searchMutation.isSuccess;

  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex flex-col flex-1 min-h-0">
        <div className="shrink-0 border-b border-border p-4 space-y-3">
          <h1 className="text-sm font-semibold">Search</h1>

          {!notebooksQuery.isLoading && !hasNotebooks ? (
            <p className="text-sm text-muted">
              Create a notebook first to search its documents.{" "}
              <Link to="/app/notebooks" className="text-accent hover:underline">
                Go to Notebooks
              </Link>
            </p>
          ) : (
            <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-3">
              <select
                value={notebookId}
                onChange={(e) => setNotebookId(e.target.value)}
                aria-label="Notebook"
                className="bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent sm:w-56"
              >
                <option value="">Select a notebook…</option>
                {notebooks.map((nb) => (
                  <option key={nb.id} value={nb.id}>
                    {nb.name}
                  </option>
                ))}
              </select>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                disabled={!notebookId}
                placeholder="Search this notebook's documents…"
                className="flex-1 bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={!notebookId || !query.trim() || searchMutation.isPending}
                className="text-sm bg-accent text-white rounded-md px-4 py-2 hover:opacity-90 disabled:opacity-50"
              >
                {searchMutation.isPending ? "…" : "Search"}
              </button>
            </form>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {!hasSearched && hasNotebooks && (
            <p className="text-muted text-sm text-center mt-8">
              Pick a notebook and enter a query to search its documents.
            </p>
          )}

          {hasSearched && results.length === 0 && (
            <p className="text-muted text-sm text-center mt-8">No matching passages found.</p>
          )}

          {results.map((hit) => (
            <button
              key={hit.chunk_id}
              type="button"
              onClick={() => openCitation(hit)}
              className="w-full text-left bg-surface border border-border rounded-md p-3 hover:border-accent transition"
            >
              <p className="text-sm font-medium truncate" title={resolveTitle(hit.document_id)}>
                {resolveTitle(hit.document_id)}
              </p>
              <p className="text-sm text-muted mt-1 line-clamp-3 whitespace-pre-wrap">
                {hit.content}
              </p>
            </button>
          ))}
        </div>
      </div>

      {/* Citation side panel: full-viewport overlay below `lg:`, restored to the
          original `w-80` side column at `lg:`+ — mirrors ChatPanel.tsx's citation
          panel treatment for a consistent responsive experience. */}
      {activeCitation && (
        <div className="fixed inset-0 z-40 bg-bg lg:static lg:inset-auto lg:z-auto lg:w-80 shrink-0 border-l border-border flex flex-col overflow-hidden">
          <CitationPanel
            citation={activeCitation}
            documents={allDocsQuery.data ?? []}
            onClose={() => setActiveCitation(null)}
          />
        </div>
      )}
    </div>
  );
}
