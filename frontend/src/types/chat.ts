// Types mirror app.schemas.chat exactly. Two citation paths share this ONE shape
// (additive — the P1 broad-query router, never a new parallel type):
// - citation_type="chunk" (default, the original F41 shape, byte-identical):
//   chunk_id/char_start/char_end are ALWAYS populated — only structurally nullable
//   (typed `| null`) to let the section path below omit them.
// - citation_type="section" (P1 broad-query map-reduce): the answer was synthesized
//   from section summaries, not individual chunks — chunk_id/char_start/char_end are
//   null and section_id/heading are populated instead.
export type ResolvedCitation = {
  marker: number;
  document_id: string;
  chunk_id: string | null;
  char_start: number | null;
  char_end: number | null;
  content: string;
  // The owning section's page range, shown alongside (not instead of) the char
  // offsets on the chunk path. Both null when the source chunk has no section or
  // the section has no page info recovered for it — never fabricated. Always null
  // on the section path (a synthesized section-level answer has no single page).
  page_start: number | null;
  page_end: number | null;
  citation_type: "chunk" | "section";
  // Populated only on the section path — always null on the unchanged chunk path.
  section_id: string | null;
  heading: string | null;
};

export type ChatResponse = {
  correlation_id: string;
  conversation_id: string;
  message_id: string;
  notebook_id: string;
  query: string;
  answer: string;
  citations: ResolvedCitation[];
  model: string;
  // Reranker-score confidence gate: true when the answer is the fixed "weak
  // evidence" message rather than a real LLM answer (citations will be empty).
  weak_evidence: boolean;
};

export type ChatRequest = {
  notebook_id: string;
  query: string;
  k?: number;
};

// SSE event shapes for the /chat/stream endpoint (F4x).
export type SSETokenEvent = { type: "token"; content: string };
export type SSEDoneEvent = ChatResponse & { type: "done" };
export type SSEErrorEvent = { type: "error"; message: string };
export type SSEEvent = SSETokenEvent | SSEDoneEvent | SSEErrorEvent;

// Chat history hydration (GET /chat/notebooks/{notebook_id}/messages) — mirrors
// app.models.chat.MessageOut. Fetched on mount so a conversation survives navigating
// away and back (nothing previously read conversations/messages back for a user).
export type ChatHistoryMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  citations: ResolvedCitation[] | null;
  created_at: string;
  // The CALLING user's own prior rating on this message — never anyone else's, never
  // an aggregate. null when this user hasn't rated it yet.
  my_feedback: "up" | "down" | null;
};

// POST /chat/messages/{message_id}/feedback — mirrors app.models.chat.FeedbackCreate.
// Only `rating` is sent by the wired-up thumbs buttons today; the other fields exist
// schema-ready on the backend for a future admin labeling UI.
export type FeedbackRequest = {
  rating: "up" | "down";
};

// F42 admin debug bundle — the persisted trace for one answer. Mirrors
// app.models.chat.MessageTraceOut.hits: list[ContextBlock] | list[SynthesisBlock] — a
// chunk-path trace (flat/hybrid/rerank, unchanged since F42) or a section-path trace
// (P1 broad-query map-reduce), never mixed within one trace. `chunk_id` is present only
// on the chunk shape, so that's the discriminator used at render time.
export type ChunkTraceHit = {
  index: number;
  document_id: string;
  chunk_id: string;
  char_start: number;
  char_end: number;
  content: string;
  // Null for a lexical-only hybrid-search hit (no cosine distance to report).
  distance: number | null;
};

export type SectionTraceHit = {
  index: number;
  content: string;
  section_ids: string[];
  headings: (string | null)[];
  document_ids: string[];
};

export type TraceHit = ChunkTraceHit | SectionTraceHit;

export type MessageTrace = {
  id: string;
  message_id: string;
  hits: TraceHit[];
  final_prompt: string;
  raw_output: string;
  created_at: string;
};
