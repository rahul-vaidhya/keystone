// Types mirror app.schemas.chat exactly.
export type ResolvedCitation = {
  marker: number;
  document_id: string;
  chunk_id: string;
  char_start: number;
  char_end: number;
  content: string;
  // The owning section's page range, shown alongside (not instead of) the char
  // offsets. Both null when the source chunk has no section or the section has no
  // page info recovered for it — never fabricated.
  page_start: number | null;
  page_end: number | null;
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

// F42 admin debug bundle — the persisted trace for one answer.
export type TraceHit = {
  index: number;
  document_id: string;
  chunk_id: string;
  char_start: number;
  char_end: number;
  content: string;
  distance: number;
};

export type MessageTrace = {
  id: string;
  message_id: string;
  hits: TraceHit[];
  final_prompt: string;
  raw_output: string;
  created_at: string;
};
