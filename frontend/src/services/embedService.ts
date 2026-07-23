import { apiFetch } from "./http";
import type {
  EmbedChatRequest,
  EmbedConfig,
  EmbedSSEEvent,
  Widget,
  WidgetCreateRequest,
  WidgetUpdateRequest,
} from "../types/embed";

// Mirrors http.ts's own extractErrorDetail — duplicated (not imported) because the
// two PUBLIC functions below deliberately do NOT go through apiFetch: an anonymous
// widget visitor has no Veratas session, so these calls must never carry an
// Authorization header or `credentials: "include"`. The embed backend's exception
// handlers (app/utils/http.py) always put a plain string in `detail` for
// WidgetNotFound/OriginNotAllowed/WidgetRateLimited, but this stays defensive
// against the Pydantic-422-array shape too.
function extractPublicErrorDetail(body: unknown, fallback: string): string {
  if (typeof body !== "object" || !body || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null,
      )
      .filter((msg): msg is string => msg !== null);
    if (messages.length > 0) return messages.join("; ");
  }
  return fallback;
}

async function parsePublicJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const embedApi = {
  // ---- admin (authed via apiFetch, owner/admin only — the backend 403s otherwise) ----

  list: () => apiFetch<Widget[]>("/embed/widgets"),

  create: (req: WidgetCreateRequest) =>
    apiFetch<Widget>("/embed/widgets", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  update: (id: string, req: WidgetUpdateRequest) =>
    apiFetch<Widget>(`/embed/widgets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(req),
    }),

  remove: (id: string) => apiFetch<void>(`/embed/widgets/${id}`, { method: "DELETE" }),

  // ---- public (no auth — served to anonymous visitors inside the embed iframe) ----

  getPublicConfig: async (orgId: string, publicId: string): Promise<EmbedConfig> => {
    const res = await fetch(`/embed/public/${orgId}/${publicId}/config`);
    const body = await parsePublicJson(res);
    if (!res.ok) {
      throw new Error(extractPublicErrorDetail(body, res.statusText));
    }
    return body as EmbedConfig;
  },

  /**
   * Streams the answer token-by-token via a BARE fetch + ReadableStream (no
   * sessionStorage token, no `credentials: "include"`) — otherwise mirrors
   * chatApi.streamAsk's exact mechanics (buffer split on "\n\n", cleanup via
   * AbortController). Non-stream failures (missing/revoked widget, disallowed
   * origin, rate limit) are real HTTP statuses returned BEFORE any SSE bytes are
   * sent (see app/controllers/embed.py's `stream_public_chat` — the validating
   * coroutine is awaited before the StreamingResponse is built) — surfaced via
   * `onError` with the status code so the caller can special-case 429.
   */
  streamPublicChat: (
    orgId: string,
    publicId: string,
    body: EmbedChatRequest,
    callbacks: {
      onToken: (token: string) => void;
      onDone: (response: Extract<EmbedSSEEvent, { type: "done" }>) => void;
      onError: (message: string, status?: number) => void;
    },
  ): (() => void) => {
    const controller = new AbortController();

    void (async () => {
      try {
        const res = await fetch(`/embed/public/${orgId}/${publicId}/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          const parsed = await parsePublicJson(res);
          callbacks.onError(
            extractPublicErrorDetail(parsed, `Request failed (${res.status})`),
            res.status,
          );
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const chunk = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            if (chunk.startsWith("data: ")) {
              try {
                const event = JSON.parse(chunk.slice(6)) as EmbedSSEEvent;
                if (event.type === "token") callbacks.onToken(event.content);
                else if (event.type === "done") callbacks.onDone(event);
                else if (event.type === "error") callbacks.onError(event.message);
              } catch {
                /* ignore malformed SSE lines */
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (err) {
        if (err instanceof Error && err.name !== "AbortError") {
          callbacks.onError("Stream disconnected");
        }
      }
    })();

    return () => controller.abort();
  },
};
