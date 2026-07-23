import type { ResolvedCitation } from "./chat";

// Mirrors app.models.embed exactly (docs/embed-widget-plan.md §7).
// One widget = one notebook + a public capability id (plaintext, public by design —
// visible in the customer's page source) + an origin allowlist + an active flag.
export type Widget = {
  id: string;
  name: string;
  knowledge_base_id: string;
  public_id: string;
  allowed_origins: string[];
  is_active: boolean;
  created_at: string;
  // Both computed server-side from settings.PUBLIC_APP_URL — never built client-side.
  embed_snippet: string;
  iframe_url: string;
};

export type WidgetCreateRequest = {
  knowledge_base_id: string;
  name: string;
  allowed_origins: string[];
};

export type WidgetUpdateRequest = {
  name?: string;
  allowed_origins?: string[];
  is_active?: boolean;
};

// Public-facing (GET /embed/public/{org_id}/{public_id}/config) — served with NO
// auth, deliberately exposes nothing about the widget/org beyond these two strings.
export type EmbedConfig = {
  widget_name: string;
  notebook_name: string;
};

// Public-facing (POST /embed/public/{org_id}/{public_id}/stream) body.
export type EmbedChatRequest = {
  query: string;
  k?: number;
  // Captured client-side from the parent frame's window.location.origin (relayed
  // through widget.js's iframe query param) and checked against allowed_origins.
  parent_origin: string;
};

// SSE event shapes for the public stream endpoint. The "done" event mirrors
// ChatResponse (app.models.chat) exactly, since public_chat_stream delegates
// straight to the existing chat_service.stream_ask.
export type EmbedSSETokenEvent = { type: "token"; content: string };
export type EmbedSSEDoneEvent = {
  type: "done";
  correlation_id: string;
  conversation_id: string;
  message_id: string;
  notebook_id: string;
  query: string;
  answer: string;
  citations: ResolvedCitation[];
  model: string;
};
export type EmbedSSEErrorEvent = { type: "error"; message: string };
export type EmbedSSEEvent = EmbedSSETokenEvent | EmbedSSEDoneEvent | EmbedSSEErrorEvent;
