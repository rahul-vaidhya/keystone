import { getStoredAccessToken } from "./http";
import type { ChatRequest, ChatResponse, SSEEvent } from "../types/chat";

export const chatApi = {
  /**
   * Streams the answer token-by-token via fetch + ReadableStream (not EventSource —
   * POST bodies require fetch). Returns a cleanup function that aborts the stream;
   * callers must invoke it on component unmount to avoid state updates on dead trees.
   */
  streamAsk: (
    params: ChatRequest,
    callbacks: {
      onToken: (token: string) => void;
      onDone: (response: ChatResponse) => void;
      onError: (message: string) => void;
    },
  ): (() => void) => {
    const controller = new AbortController();

    void (async () => {
      const headers = new Headers({ "Content-Type": "application/json" });
      const token = getStoredAccessToken();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      try {
        const res = await fetch("/chat/stream", {
          method: "POST",
          headers,
          body: JSON.stringify(params),
          signal: controller.signal,
          credentials: "include",
        });
        if (!res.ok || !res.body) {
          callbacks.onError(`Request failed (${res.status})`);
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
                const event = JSON.parse(chunk.slice(6)) as SSEEvent;
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
