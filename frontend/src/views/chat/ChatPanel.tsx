import { useEffect, useRef, useState } from "react";
import { chatApi } from "../../controllers/chatController";
import type { ChatResponse, ResolvedCitation } from "../../models/chat";
import type { Document } from "../../models/documents";
import { CitationPanel } from "./CitationPanel";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: ResolvedCitation[];
};

// Splits answer text at [n] markers and renders resolved citations as clickable buttons.
function AnswerText({
  content,
  citations,
  onCitationClick,
}: {
  content: string;
  citations: ResolvedCitation[];
  onCitationClick: (c: ResolvedCitation) => void;
}) {
  const parts = content.split(/(\[\d+\])/);
  return (
    <span className="whitespace-pre-wrap">
      {parts.map((part, i) => {
        const match = /^\[(\d+)\]$/.exec(part);
        if (match) {
          const marker = parseInt(match[1], 10);
          const citation = citations.find((c) => c.marker === marker);
          return citation ? (
            <button
              key={i}
              type="button"
              onClick={() => onCitationClick(citation)}
              className="font-mono text-accent text-xs hover:underline align-super px-0.5"
            >
              [{marker}]
            </button>
          ) : (
            <span key={i} className="font-mono text-muted text-xs">
              {part}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </span>
  );
}

export function ChatPanel({
  notebookId,
  documents,
}: {
  notebookId: string;
  documents: Document[];
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeCitation, setActiveCitation] = useState<ResolvedCitation | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Abort in-progress stream on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.();
    };
  }, []);

  // Auto-scroll to bottom whenever messages update.
  // Use ?.() so jsdom (which doesn't implement scrollIntoView) doesn't throw in tests.
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || isStreaming) return;

    setQuery("");
    setIsStreaming(true);
    setActiveCitation(null);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: q },
      { role: "assistant", content: "" },
    ]);

    let accumulated = "";
    abortRef.current?.();
    abortRef.current = chatApi.streamAsk(
      { notebook_id: notebookId, query: q },
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
        onDone(response: ChatResponse) {
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = {
              role: "assistant",
              content: response.answer,
              citations: response.citations,
            };
            return next;
          });
          setIsStreaming(false);
        },
        onError(msg) {
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = {
              role: "assistant",
              content: `Error: ${msg}`,
              citations: [],
            };
            return next;
          });
          setIsStreaming(false);
        },
      },
    );
  }

  const showCitationPanel = activeCitation !== null;

  return (
    <div className="flex flex-1 min-h-0">
      {/* Chat area */}
      <div className="flex flex-col flex-1 min-h-0">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <p className="text-muted text-sm text-center mt-8">
              Ask a question about the documents in this notebook.
            </p>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] rounded-lg px-4 py-3 text-sm ${
                  msg.role === "user"
                    ? "bg-accent text-white"
                    : "bg-surface border border-border text-text"
                }`}
              >
                {msg.role === "assistant" && msg.content === "" && isStreaming ? (
                  <span className="text-muted animate-pulse">●●●</span>
                ) : msg.role === "assistant" && msg.citations !== undefined ? (
                  <AnswerText
                    content={msg.content}
                    citations={msg.citations}
                    onCitationClick={setActiveCitation}
                  />
                ) : (
                  <span className="whitespace-pre-wrap">{msg.content}</span>
                )}
              </div>
            </div>
          ))}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <form
          onSubmit={handleSubmit}
          className="shrink-0 border-t border-border p-4 flex gap-3"
        >
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
            className="text-sm bg-accent text-white rounded-md px-4 py-2 hover:opacity-90 disabled:opacity-50"
          >
            {isStreaming ? "…" : "Ask"}
          </button>
        </form>
      </div>

      {/* Citation side panel */}
      {showCitationPanel && activeCitation && (
        <div className="w-80 shrink-0 border-l border-border flex flex-col overflow-hidden">
          <CitationPanel
            citation={activeCitation}
            documents={documents}
            onClose={() => setActiveCitation(null)}
          />
        </div>
      )}
    </div>
  );
}
