import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { notebooksApi } from "../services/notebooksService";
import { documentsApi } from "../services/documentsService";
import { retrievalApi } from "../services/retrievalService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";
import type { ContextBlock, SparseMode, SparseSearchResult } from "../types/retrieval";
import type { ResolvedCitation } from "../types/chat";
import { CitationPanel } from "../components/CitationPanel";
import { DEFAULT_RANKED_OPTIONS, SparseOptions, type RankedOptions } from "../components/SparseOptions";
import { IndexStatsBar, QueryAnalysisPanel } from "../components/QueryAnalysisPanel";
import { SparseResultCard } from "../components/SparseResultCard";

type SearchMode = "semantic" | SparseMode;

const MODES: { id: SearchMode; label: string; placeholder: string; hint: string }[] = [
  {
    id: "semantic",
    label: "Semantic (hybrid)",
    placeholder: "Search this notebook's documents…",
    hint: "Dense embedding kNN (fused with the lexical channel when hybrid search is on).",
  },
  {
    id: "ranked",
    label: "Ranked (tf-idf / BM25)",
    placeholder: "e.g. octet rule",
    hint: "Free-text ranked retrieval over the from-scratch positional zone index — term-at-a-time scoring, top-K via a heap.",
  },
  {
    id: "boolean",
    label: "Boolean",
    placeholder: "e.g. octet AND rule NOT ionic",
    hint: "Uppercase AND / OR / NOT (adjacent words are ANDed). Postings are merged in increasing-df order.",
  },
  {
    id: "phrase",
    label: "Phrase",
    placeholder: 'e.g. "hydrogen bond"',
    hint: "Exact phrase via the positional index: intersect postings, then verify consecutive positions.",
  },
];

export function SearchPage() {
  const dialog = useDialog();
  const [notebookId, setNotebookId] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("semantic");
  const [ranked, setRanked] = useState<RankedOptions>(DEFAULT_RANKED_OPTIONS);
  const [activeCitation, setActiveCitation] = useState<ResolvedCitation | null>(null);

  const notebooksQuery = useQuery({ queryKey: ["notebooks"], queryFn: notebooksApi.list });

  const allDocsQuery = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.listDocuments(),
  });

  const onError = (err: unknown) =>
    void dialog.alert(err instanceof ApiError ? err.message : "Search failed. Please try again.");

  const searchMutation = useMutation({
    mutationFn: () =>
      retrievalApi.search({ notebook_id: notebookId, query: query.trim(), k: 8 }),
    onError,
  });

  const sparseMutation = useMutation({
    mutationFn: (m: SparseMode) =>
      retrievalApi.sparseSearch({
        notebook_id: notebookId,
        query: query.trim(),
        mode: m,
        k: 10,
        scheme: ranked.scheme,
        use_champions: ranked.useChampions,
        idf_threshold: ranked.idfThreshold,
        zone_weights: { heading: ranked.headingWeight, body: ranked.bodyWeight },
      }),
    onError,
  });

  const modeInfo = MODES.find((m) => m.id === mode)!;
  const isSemantic = mode === "semantic";
  const pending = isSemantic ? searchMutation.isPending : sparseMutation.isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!notebookId || !query.trim()) return;
    setActiveCitation(null);
    if (isSemantic) searchMutation.mutate();
    else sparseMutation.mutate(mode);
  }

  function switchMode(next: SearchMode) {
    setMode(next);
    setActiveCitation(null);
    sparseMutation.reset();
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
      // /retrieval/search is a direct kNN chunk lookup, never the P1 map-reduce path —
      // always a real chunk citation.
      citation_type: "chunk",
      section_id: null,
      heading: null,
    });
  }

  function openSparse(r: SparseSearchResult) {
    setActiveCitation({
      marker: r.rank,
      document_id: r.document_id,
      chunk_id: r.chunk_id,
      char_start: r.char_start,
      char_end: r.char_end,
      content: r.content,
      // Same chat-citation page derivation, computed server-side.
      page_start: r.page_start,
      page_end: r.page_end,
      citation_type: "chunk",
      section_id: null,
      heading: null,
    });
  }

  const notebooks = notebooksQuery.data ?? [];
  const hasNotebooks = notebooks.length > 0;
  const results = searchMutation.data?.results ?? [];
  const hasSearched = searchMutation.isSuccess;
  const sparse = sparseMutation.data;

  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex flex-col flex-1 min-h-0 min-w-0">
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
            <>
              <div
                role="tablist"
                aria-label="Retrieval model"
                className="flex gap-1 overflow-x-auto border-b border-border -mx-4 px-4"
              >
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    role="tab"
                    aria-selected={mode === m.id}
                    onClick={() => switchMode(m.id)}
                    className={`whitespace-nowrap text-xs px-3 py-2 -mb-px border-b-2 transition ${
                      mode === m.id
                        ? "border-accent text-text"
                        : "border-transparent text-muted hover:text-text"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

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
                  aria-label="Query"
                  placeholder={modeInfo.placeholder}
                  className="flex-1 min-w-0 bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={!notebookId || !query.trim() || pending}
                  className="text-sm bg-accent text-white rounded-md px-4 py-2 hover:opacity-90 disabled:opacity-50"
                >
                  {pending ? "…" : "Search"}
                </button>
              </form>
              <p className="text-xs text-muted">{modeInfo.hint}</p>
              {mode === "ranked" && <SparseOptions value={ranked} onChange={setRanked} />}
            </>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {isSemantic && (
            <>
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
            </>
          )}

          {!isSemantic && (
            <>
              {!sparse && hasNotebooks && (
                <p className="text-muted text-sm text-center mt-8">
                  Pick a notebook and run a {modeInfo.label.toLowerCase()} query to see every
                  indexing and query-processing step.
                </p>
              )}
              {sparse && (
                <>
                  <IndexStatsBar stats={sparse.index_stats} total={sparse.total_matches} />
                  <QueryAnalysisPanel analysis={sparse.analysis} mode={sparse.mode} />
                  {sparse.results.length === 0 ? (
                    <p className="text-muted text-sm text-center mt-6">
                      No matching passages found.
                    </p>
                  ) : (
                    sparse.results.map((r) => (
                      <SparseResultCard key={r.chunk_id} result={r} onOpen={openSparse} />
                    ))
                  )}
                </>
              )}
            </>
          )}
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
