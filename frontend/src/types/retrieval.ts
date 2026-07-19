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
  distance: number;
};

export type RetrievalSearchResponse = {
  query: string;
  results: ContextBlock[];
};
