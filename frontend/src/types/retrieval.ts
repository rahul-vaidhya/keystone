// Mirrors app.models.retrieval exactly (RetrievalSearchRequest/ContextBlock/
// RetrievalSearchResponse). UUIDs are `string` over the wire, same convention as
// ResolvedCitation in types/chat.ts.
export type RetrievalSearchRequest = {
  notebook_id: string;
  query: string;
  k?: number;
};

export type ContextBlock = {
  index: number;
  document_id: string;
  chunk_id: string;
  char_start: number;
  char_end: number;
  content: string;
  // null for a lexical-only hybrid hit (no cosine distance).
  distance: number | null;
  rerank_score?: number | null;
  // Search-page only (app.models.retrieval.SearchResultBlock): the chat-citation page
  // derivation, null when unknown.
  page_start?: number | null;
  page_end?: number | null;
};

export type RetrievalSearchResponse = {
  query: string;
  results: ContextBlock[];
};

// ---- /retrieval/sparse-search — mirrors app.models.retrieval Sparse* exactly ----------

export type SparseMode = "ranked" | "boolean" | "phrase";
export type SparseScheme = "tfidf" | "bm25";

export type SparseSearchRequest = {
  notebook_id: string;
  query: string;
  mode: SparseMode;
  scheme?: SparseScheme;
  k?: number;
  use_champions?: boolean;
  idf_threshold?: number;
  zone_weights?: { heading: number; body: number };
};

export type SparseTermStat = {
  term: string;
  surface: string[];
  query_tf: number;
  df: number;
  idf: number;
  postings: Record<string, number>;
  champions: Record<string, number>;
  eliminated: boolean;
};

export type SparseRankedTrace = {
  scheme: SparseScheme;
  zone_weights: Record<string, number>;
  use_champions: boolean;
  idf_threshold: number;
  active_terms: string[];
  eliminated_terms: string[];
  docs_with_postings: number;
  champion_candidates: number | null;
  docs_scored: number;
};

export type SparseBooleanStep = { op: string; term: string; df: number; result_size: number };

export type SparseBooleanTrace = {
  operators: string[];
  clauses: {
    operands: { word: string; terms: string[]; negated: boolean }[];
    steps: SparseBooleanStep[];
    result_size: number;
  }[];
  union_steps: SparseBooleanStep[];
};

export type SparsePhraseTrace = {
  phrase: string;
  terms: { term: string; offset: number }[];
  zones: { zone: string; postings: Record<string, number>; candidates: number; matched: number }[];
  candidates: number;
  matched: number;
};

export type SparseQueryAnalysis = {
  raw_tokens: string[];
  casefolded: string[];
  stop_words_removed: string[];
  kept_tokens: string[];
  stems: string[];
  terms: SparseTermStat[];
  ranked: SparseRankedTrace | null;
  boolean: SparseBooleanTrace | null;
  phrase: SparsePhraseTrace | null;
};

export type SparseIndexStats = {
  n_docs: number;
  vocabulary_size: number;
  avg_postings_length: number;
  zones: string[];
  champion_r: number;
  cached: boolean;
  build_ms: number;
  query_ms: number;
  total_ms: number;
};

export type SparseContribution = {
  term: string;
  zone: string;
  tf: number;
  idf: number;
  weight: number;
};

export type SparseSearchResult = {
  rank: number;
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  heading: string | null;
  content: string;
  snippet: string;
  char_start: number;
  char_end: number;
  page_start: number | null;
  page_end: number | null;
  score: number | null;
  contributions: SparseContribution[];
  matched_terms: string[];
  highlights: string[];
  phrase_matches: { zone: string; positions: number[] }[];
};

export type SparseSearchResponse = {
  query: string;
  mode: SparseMode;
  analysis: SparseQueryAnalysis;
  index_stats: SparseIndexStats;
  total_matches: number;
  results: SparseSearchResult[];
};
