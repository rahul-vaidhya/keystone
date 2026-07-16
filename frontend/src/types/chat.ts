// Types mirror app.schemas.chat exactly.
export type ResolvedCitation = {
  marker: number;
  document_id: string;
  chunk_id: string;
  char_start: number;
  char_end: number;
  content: string;
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
