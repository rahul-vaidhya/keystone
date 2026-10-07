import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { notebooksApi } from "../services/notebooksService";
import { documentsApi } from "../services/documentsService";
import { retrievalApi } from "../services/retrievalService";
import { useDialog } from "../hooks/useDialog";
import { ApiError } from "../types/auth";
import type {
  ContextBlock,
  SparseMode,
  SparseSearchResponse,
  SparseSearchResult,
} from "../types/retrieval";
import type { ResolvedCitation } from "../types/chat";
import { CitationPanel } from "../components/CitationPanel";
import { DEFAULT_RANKED_OPTIONS, SparseOptions, type RankedOptions } from "../components/SparseOptions";
import { IndexStatsBar, QueryAnalysisPanel } from "../components/QueryAnalysisPanel";
import { SparseResultCard } from "../components/SparseResultCard";
import { cleanParserMarkdown } from "../components/HighlightedText";
import { TIPS, Tip } from "../components/Tip";

type SearchMode = "semantic" | SparseMode;

const PAGE_SIZE = 10;
const MAX_K = 50;

const BOOLEAN_SYNTAX =
  "Syntax: term AND term · term OR term · NOT term — a term on both sides of AND/OR, no parentheses; evaluated left to right, AND before OR.";

function pageLabel(start?: number | null, end?: number | null): string | null {
  if (start === null || start === undefined) return null;
  return end === null || end === undefined || end === start ? `p. ${start}` : `pp. ${start}–${end}`;
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{children}</h2>;
}

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
  const [sparseK, setSparseK] = useState(PAGE_SIZE);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [sparse, setSparse] = useState<SparseSearchResponse | null>(null);

  const notebooksQuery = useQuery({ queryKey: ["notebooks"], queryFn: notebooksApi.list });

  const allDocsQuery = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.listDocuments(),
  });

  const onError = (err: unknown) => {
    // A malformed query (422 from the Boolean validator) is the user's to fix: show it
    // inline under the query box rather than as a blocking dialog.
    if (err instanceof ApiError && err.status === 422) {
      setInlineError(err.message);
      setSparse(null);
      return;
    }
    void dialog.alert(err instanceof ApiError ? err.message : "Search failed. Please try again.");
  };

  const searchMutation = useMutation({
    mutationFn: () =>
      retrievalApi.search({ notebook_id: notebookId, query: query.trim(), k: 8 }),
    onError,
  });

  const sparseMutation = useMutation({
    mutationFn: ({ m, k, q }: { m: SparseMode; k: number; q: string }) =>
      retrievalApi.sparseSearch({
        notebook_id: notebookId,
        query: q,
        mode: m,
        k,
        scheme: ranked.scheme,
        use_champions: ranked.useChampions,
        idf_threshold: ranked.idfThreshold,
        zone_weights: { heading: ranked.headingWeight, body: ranked.bodyWeight },
      }),
    // Kept in state (not read from mutation.data) so "Show more" doesn't blank the page
    // while the larger top-K is fetched.
    onSuccess: (data) => setSparse(data),
    onError,
  });

  const modeInfo = MODES.find((m) => m.id === mode)!;
  const isSemantic = mode === "semantic";
  const pending = isSemantic ? searchMutation.isPending : sparseMutation.isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!notebookId || !query.trim()) return;
    setActiveCitation(null);
    setInlineError(null);
    if (isSemantic) searchMutation.mutate();
    else {
      setSparseK(PAGE_SIZE);
      sparseMutation.mutate({ m: mode, k: PAGE_SIZE, q: query.trim() });
    }
  }

  function showMore() {
    if (isSemantic || !sparse) return;
    const next = Math.min(MAX_K, sparseK + PAGE_SIZE);
    setSparseK(next);
    sparseMutation.mutate({ m: sparse.mode, k: next, q: sparse.query });
  }

  function switchMode(next: SearchMode) {
    setMode(next);
    setActiveCitation(null);
    setInlineError(null);
    sparseMutation.reset();
    setSparse(null);
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
      // Same chat-citation page derivation, resolved server-side (null when unknown).
      page_start: hit.page_start ?? null,
      page_end: hit.page_end ?? null,
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
                className="grid grid-cols-2 sm:flex sm:gap-1 border-b border-border -mx-4 px-4"
              >
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    role="tab"
                    aria-selected={mode === m.id}
                    onClick={() => switchMode(m.id)}
                    className={`whitespace-nowrap text-center text-xs px-3 py-2 -mb-px border-b-2 transition ${
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
              {mode === "boolean" && (
                <p className="text-xs text-muted" data-testid="boolean-syntax">
                  {BOOLEAN_SYNTAX}
                </p>
              )}
              {inlineError && (
                <p
                  role="alert"
                  className="text-xs text-danger border border-danger rounded-md px-3 py-2"
                >
                  {inlineError}
                </p>
              )}
              {mode === "ranked" && <SparseOptions value={ranked} onChange={setRanked} />}
            </>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
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

              {results.length > 0 && (
                <section className="space-y-3" aria-label="Results">
                  <SectionHeading>
                    Results
                    <span className="normal-case font-normal tracking-normal">
                      {" "}
                      · top {results.length}, ordered by{" "}
                      {results[0].rerank_score != null
                        ? "reranker score"
                        : results[0].fused_score != null
                          ? "fused RRF score (dense + BM25)"
                          : "cosine distance"}
                    </span>
                  </SectionHeading>
                  {results.map((hit) => {
                    const page = pageLabel(hit.page_start, hit.page_end);
                    return (
                      <button
                        key={hit.chunk_id}
                        type="button"
                        onClick={() => openCitation(hit)}
                        aria-label={`Open source #${hit.index}`}
                        className="w-full text-left bg-surface border border-border rounded-md p-3 space-y-1 hover:border-accent transition"
                      >
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono text-muted">#{hit.index}</span>
                          <span
                            className="text-sm font-medium truncate flex-1"
                            title={resolveTitle(hit.document_id)}
                          >
                            {resolveTitle(hit.document_id)}
                          </span>
                          {page && <span className="text-muted shrink-0">{page}</span>}
                          {hit.rerank_score !== null && hit.rerank_score !== undefined ? (
                            <span className="shrink-0 text-muted">
                              <Tip tip={TIPS.rerank}>rerank</Tip>{" "}
                              <span className="font-mono text-accent">
                                {hit.rerank_score.toFixed(4)}
                              </span>
                            </span>
                          ) : hit.fused_score != null ? (
                            <span className="shrink-0 text-muted">
                              <Tip tip={TIPS.fused}>RRF</Tip>{" "}
                              <span className="font-mono text-accent">
                                {hit.fused_score.toFixed(4)}
                              </span>
                            </span>
                          ) : hit.distance !== null ? (
                            <span className="shrink-0 text-muted">
                              <Tip tip={TIPS.distance}>cos distance</Tip>{" "}
                              <span className="font-mono text-accent">
                                {hit.distance.toFixed(4)}
                              </span>
                            </span>
                          ) : (
                            <span className="shrink-0 text-muted">lexical match</span>
                          )}
                        </div>
                        {hit.fused_score != null && (
                          <div className="flex flex-wrap gap-x-4 text-xs text-muted">
                            <span>
                              <Tip tip={TIPS.dense}>dense</Tip>{" "}
                              {hit.vector_rank != null ? (
                                <span className="font-mono">
                                  #{hit.vector_rank}
                                  {hit.distance !== null && ` · cos dist ${hit.distance.toFixed(4)}`}
                                </span>
                              ) : (
                                "not in dense list"
                              )}
                            </span>
                            <span>
                              <Tip tip={TIPS.lexical}>BM25</Tip>{" "}
                              {hit.lexical_rank != null ? (
                                <span className="font-mono">
                                  #{hit.lexical_rank}
                                  {hit.sparse_score != null && ` · score ${hit.sparse_score.toFixed(3)}`}
                                </span>
                              ) : (
                                "not in BM25 list"
                              )}
                            </span>
                          </div>
                        )}
                        <p className="text-sm text-muted line-clamp-3 whitespace-pre-wrap">
                          {cleanParserMarkdown(hit.content)}
                        </p>
                      </button>
                    );
                  })}
                </section>
              )}
            </>
          )}

          {!isSemantic && (
            <>
              {!sparse && !inlineError && hasNotebooks && (
                <p className="text-muted text-sm text-center mt-8">
                  Pick a notebook and run a {modeInfo.label.toLowerCase()} query to see every
                  indexing and query-processing step.
                </p>
              )}
              {sparse && (
                <>
                  <section className="space-y-2">
                    <SectionHeading>Index statistics</SectionHeading>
                    <IndexStatsBar stats={sparse.index_stats} />
                  </section>
                  <QueryAnalysisPanel analysis={sparse.analysis} mode={sparse.mode} />
                  <section className="space-y-3" aria-label="Results">
                    <SectionHeading>
                      Results
                      {sparse.results.length > 0 && (
                        <span
                          className="normal-case font-normal tracking-normal"
                          data-testid="results-count"
                        >
                          {" "}
                          · showing{" "}
                          {sparse.results.length < sparse.total_matches
                            ? `top ${sparse.results.length} of ${sparse.total_matches}`
                            : `all ${sparse.total_matches}`}{" "}
                          matching chunk{sparse.total_matches === 1 ? "" : "s"}
                          {sparse.mode === "ranked" ? ", by score" : ", in document order"}
                        </span>
                      )}
                    </SectionHeading>
                    {sparse.results.length === 0 ? (
                      <p className="text-muted text-sm text-center mt-6">
                        No matching passages found.
                      </p>
                    ) : (
                      sparse.results.map((r) => (
                        <SparseResultCard key={r.chunk_id} result={r} onOpen={openSparse} />
                      ))
                    )}
                    {sparse.results.length < sparse.total_matches && sparseK < MAX_K && (
                      <div className="flex justify-center">
                        <button
                          type="button"
                          onClick={showMore}
                          disabled={pending}
                          className="text-xs border border-border rounded-md px-4 py-2 text-muted hover:text-text hover:border-accent disabled:opacity-50"
                        >
                          {pending ? "Loading…" : `Show more (up to ${Math.min(MAX_K, sparseK + PAGE_SIZE)})`}
                        </button>
                      </div>
                    )}
                  </section>
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
