import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { embedApi } from "../services/embedService";
import type { EmbedConfig } from "../types/embed";

// Public, unauthenticated page rendered standalone inside widget.js's iframe — no
// AppShell/Sidebar, no useAuth/useDialog (this component must never need any app
// context an anonymous website visitor couldn't possibly have). Deliberately NOT
// built on top of ChatPanel.tsx, which is coupled to auth/documents/dialog context.
type Message = {
  role: "user" | "assistant";
  content: string;
  isError?: boolean;
};

type ConfigState = "loading" | "ready" | "unavailable";

// Splits answer text at [n] markers and renders them as plain (non-clickable)
// superscripts — MVP scope per the plan; no CitationPanel, no click-through.
function renderAnswer(content: string): ReactNode[] {
  const parts = content.split(/(\[\d+\])/);
  return parts.map((part, i) => {
    if (/^\[\d+\]$/.test(part)) {
      return (
        <sup key={i} className="font-mono text-accent text-[0.7em]">
          {part}
        </sup>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export function EmbedChatPage() {
  const [searchParams] = useSearchParams();
  const org = searchParams.get("org");
  const widgetId = searchParams.get("widget");
  const parentOrigin = searchParams.get("parent") ?? "";

  const [configState, setConfigState] = useState<ConfigState>(
    org && widgetId ? "loading" : "unavailable",
  );
  const [config, setConfig] = useState<EmbedConfig | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [query, setQuery] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!org || !widgetId) {
      setConfigState("unavailable");
      return;
    }
    let cancelled = false;
    setConfigState("loading");
    embedApi
      .getPublicConfig(org, widgetId)
      .then((cfg) => {
        if (cancelled) return;
        setConfig(cfg);
        setConfigState("ready");
      })
      .catch(() => {
        if (!cancelled) setConfigState("unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [org, widgetId]);

  // Abort any in-progress stream on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.();
    };
  }, []);

  // Auto-scroll to bottom on every new message/token. Optional-chain the method
  // itself (not just the ref) — jsdom doesn't implement scrollIntoView.
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages]);

  function submitQuery(rawQuery: string) {
    const q = rawQuery.trim();
    if (!q || isStreaming || configState !== "ready" || !org || !widgetId) return;

    setQuery("");
    setIsStreaming(true);
    setMessages((prev) => [...prev, { role: "user", content: q }, { role: "assistant", content: "" }]);

    let accumulated = "";
    abortRef.current?.();
    abortRef.current = embedApi.streamPublicChat(
      org,
      widgetId,
      { query: q, parent_origin: parentOrigin },
      {
        onToken(token) {
          accumulated += token;
          const snap = accumulated;
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", content: snap };
            return next;
          });
        },
        onDone(response) {
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", content: response.answer };
            return next;
          });
          setIsStreaming(false);
        },
        onError(_message, status) {
          const friendly =
            status === 429
              ? "You're sending messages too quickly — please wait a moment."
              : "Sorry — something went wrong. Please try again.";
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", content: friendly, isError: true };
            return next;
          });
          setIsStreaming(false);
        },
      },
    );
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submitQuery(query);
  }

  if (configState === "unavailable") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-muted text-sm p-6 text-center">
        This chatbot is unavailable.
      </div>
    );
  }

  if (configState === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-muted text-sm">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-bg">
      <header className="shrink-0 border-b border-border px-4 py-3">
        <p className="text-sm font-medium truncate">{config?.widget_name}</p>
        {config?.notebook_name && (
          <p className="text-xs text-muted truncate">{config.notebook_name}</p>
        )}
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-muted text-sm text-center mt-8">Ask a question to get started.</p>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                msg.role === "user"
                  ? "bg-accent text-white"
                  : msg.isError
                    ? "bg-surface border border-danger/40 text-danger"
                    : "bg-surface border border-border text-text"
              }`}
            >
              {msg.role === "assistant" && msg.content === "" && isStreaming ? (
                <span className="text-muted animate-pulse">●●●</span>
              ) : (
                <span className="whitespace-pre-wrap">{renderAnswer(msg.content)}</span>
              )}
            </div>
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="shrink-0 border-t border-border p-3 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={isStreaming}
          placeholder="Ask a question…"
          className="flex-1 bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={isStreaming || !query.trim()}
          className="text-sm bg-accent text-white rounded-md px-3 py-2 hover:opacity-90 disabled:opacity-50"
        >
          {isStreaming ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
