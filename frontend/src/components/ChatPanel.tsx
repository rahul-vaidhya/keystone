import { useEffect, useRef, useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { chatApi } from "../services/chatService";
import { evalsApi } from "../services/evalsService";
import type {
  ChatHistoryMessage,
  ChatResponse,
  ClaimCheck,
  MessageTrace,
  ResolvedCitation,
} from "../types/chat";
import type { Document } from "../types/documents";
import { CitationPanel } from "./CitationPanel";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: ResolvedCitation[];
  messageId?: string;
  // Reranker-score confidence gate (P1): true only on a just-streamed answer whose
  // ChatResponse carried weak_evidence — history hydration has no equivalent signal
  // (MessageOut doesn't persist it), so a reloaded weak-evidence answer shows no badge.
  weakEvidence?: boolean;
  // Per-sentence citation check results (null/undefined when the checker is off).
  claimChecks?: ClaimCheck[] | null;
};

// Finding A (Medium, UX audit): static, generic starter questions shown the instant a
// notebook has at least one attached document but no conversation yet — removes the
// "blank page" first-ask friction NotebookLM solves with LLM-generated suggestions.
// Deliberately static (not per-notebook LLM-generated): avoids a hidden per-view LLM
// cost, consistent with this project's "wait for real evidence before building" bias
// (see memory.md — the reranker seam and AI chunk-enrichment decisions).
const STARTER_QUESTIONS = [
  "Summarize the key points in these documents",
  "What are the main topics covered?",
  "What definitions or important terms are explained here?",
];

// F42 admin debug bundle — collapsible, fetched lazily on first open. Cached in this
// module-level map (not component state, which resets when the toggle closes and
// unmounts this component) so re-opening the same message's trace never re-fetches.
const traceCache = new Map<string, MessageTrace>();

function TraceDetails({ messageId }: { messageId: string }) {
  const [trace, setTrace] = useState<MessageTrace | null>(traceCache.get(messageId) ?? null);
  const [error, setError] = useState<string | null>(null);
  // Golden-eval curation: fire-and-submit, no persisted/cached state needed beyond this
  // render — re-opening the trace after a successful curation just shows "Add to golden
  // set" again, which is harmless (curation isn't idempotency-sensitive from the UI's
  // point of view; a second click just adds a second golden question).
  const [curationState, setCurationState] = useState<"idle" | "pending" | "done" | "error">(
    "idle",
  );

  useEffect(() => {
    if (traceCache.has(messageId)) return;
    let cancelled = false;
    chatApi
      .getTrace(messageId)
      .then((t) => {
        traceCache.set(messageId, t);
        if (!cancelled) setTrace(t);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load trace.");
      });
    return () => {
      cancelled = true;
    };
  }, [messageId]);

  function handleAddToGoldenSet() {
    setCurationState("pending");
    evalsApi
      .addGoldenQuestion(messageId)
      .then(() => setCurationState("done"))
      .catch(() => setCurationState("error"));
  }

  if (error) return <p className="text-xs text-red-500 mt-2">{error}</p>;
  if (!trace) return <p className="text-xs text-muted mt-2 animate-pulse">Loading trace…</p>;

  return (
    <div className="mt-2 border border-border rounded-md bg-bg text-xs space-y-2 p-3">
      <div>
        <p className="font-medium text-muted mb-1">Hits ({trace.hits.length})</p>
        <ul className="space-y-1">
          {trace.hits.map((hit) =>
            "chunk_id" in hit ? (
              <li key={hit.chunk_id} className="font-mono text-muted">
                [{hit.index}] doc {hit.document_id.slice(0, 8)}…
                {hit.distance !== null && <> · distance {hit.distance.toFixed(3)}</>}
                {hit.rerank_score != null && <> · rerank {hit.rerank_score.toFixed(3)}</>}
                {hit.sparse_score != null && <> · sparse {hit.sparse_score.toFixed(3)}</>}
                {hit.sparse_explanation && hit.sparse_explanation.length > 0 && (
                  <table
                    className="mt-1 mb-2 border-collapse text-[11px]"
                    aria-label={`Term contributions for hit ${hit.index}`}
                  >
                    <thead>
                      <tr className="text-left">
                        <th className="pr-3 font-medium">term</th>
                        <th className="pr-3 font-medium">zone</th>
                        <th className="pr-3 font-medium text-right">tf</th>
                        <th className="pr-3 font-medium text-right">idf</th>
                        <th className="font-medium text-right">weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {hit.sparse_explanation.map((c) => (
                        <tr key={`${c.term}-${c.zone}`}>
                          <td className="pr-3">{c.term}</td>
                          <td className="pr-3">{c.zone}</td>
                          <td className="pr-3 text-right">{c.tf}</td>
                          <td className="pr-3 text-right">{c.idf.toFixed(3)}</td>
                          <td className="text-right">{c.weight.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </li>
            ) : (
              <li key={hit.index} className="font-mono text-muted">
                [{hit.index}]{" "}
                {hit.headings.filter(Boolean).join(", ") ||
                  `${hit.section_ids.length} section(s)`}
              </li>
            ),
          )}
        </ul>
      </div>
      <div>
        <p className="font-medium text-muted mb-1">Final prompt</p>
        <pre className="whitespace-pre-wrap font-mono text-muted max-h-40 overflow-y-auto">
          {trace.final_prompt}
        </pre>
      </div>
      <div>
        <p className="font-medium text-muted mb-1">Raw output</p>
        <pre className="whitespace-pre-wrap font-mono text-muted max-h-40 overflow-y-auto">
          {trace.raw_output}
        </pre>
      </div>
      <div>
        <button
          type="button"
          onClick={handleAddToGoldenSet}
          disabled={curationState === "pending" || curationState === "done"}
          className="text-xs text-accent hover:underline disabled:no-underline disabled:text-muted disabled:cursor-default"
        >
          {curationState === "done"
            ? "Added ✓"
            : curationState === "pending"
              ? "Adding…"
              : curationState === "error"
                ? "Failed — try again"
                : "Add to golden set"}
        </button>
      </div>
    </div>
  );
}

// Splits answer text at [n] markers and renders resolved citations as clickable buttons.
// Weak/uncited sentences from the per-sentence citation check are wrapped in an amber
// dotted underline + ⚠ whose title tooltip shows the lexical/semantic/combined scores.
function fmtScore(v: number | null): string {
  return v === null ? "n/a" : v.toFixed(2);
}

function claimTooltip(check: ClaimCheck): string {
  if (check.status === "uncited") return "Uncited claim: this sentence cites no source.";
  return `Weak support for cited source(s) ${check.citations.map((n) => `[${n}]`).join("")} — lexical ${fmtScore(check.lexical)} · semantic ${fmtScore(check.semantic)} · combined ${fmtScore(check.score)}`;
}

function AnswerText({
  content,
  citations,
  onCitationClick,
  claimChecks,
}: {
  content: string;
  citations: ResolvedCitation[];
  onCitationClick: (c: ResolvedCitation) => void;
  claimChecks?: ClaimCheck[] | null;
}) {
  // Partition the answer into plain and flagged segments by locating each flagged
  // sentence (backend sentences are verbatim substrings of the answer) in order.
  const segments: { text: string; check?: ClaimCheck }[] = [];
  let cursor = 0;
  for (const check of claimChecks ?? []) {
    if (check.status === "supported") continue;
    const at = content.indexOf(check.sentence, cursor);
    if (at === -1) continue;
    if (at > cursor) segments.push({ text: content.slice(cursor, at) });
    segments.push({ text: check.sentence, check });
    cursor = at + check.sentence.length;
  }
  if (cursor < content.length) segments.push({ text: content.slice(cursor) });

  return (
    <span className="whitespace-pre-wrap">
      {segments.map((seg, si) =>
        seg.check ? (
          <span
            key={si}
            data-claim-status={seg.check.status}
            title={claimTooltip(seg.check)}
            className="underline decoration-dotted decoration-warning underline-offset-2"
          >
            <MarkedText
              text={seg.text}
              citations={citations}
              onCitationClick={onCitationClick}
            />
            <span className="text-warning text-xs ml-0.5" aria-hidden="true">
              ⚠
            </span>
          </span>
        ) : (
          <MarkedText
            key={si}
            text={seg.text}
            citations={citations}
            onCitationClick={onCitationClick}
          />
        ),
      )}
    </span>
  );
}

function ClaimCheckSummary({ checks }: { checks: ClaimCheck[] }) {
  const count = (s: ClaimCheck["status"]) => checks.filter((c) => c.status === s).length;
  return (
    <p className="text-xs text-muted mt-1" data-testid="claim-check-summary">
      Claim check: {count("supported")} supported · {count("weak")} weak ·{" "}
      {count("uncited")} uncited
    </p>
  );
}

function MarkedText({
  text,
  citations,
  onCitationClick,
}: {
  text: string;
  citations: ResolvedCitation[];
  onCitationClick: (c: ResolvedCitation) => void;
}) {
  const parts = text.split(/(\[\d+\])/);
  return (
    <>
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
    </>
  );
}

export function ChatPanel({
  notebookId,
  documents,
}: {
  notebookId: string;
  documents: Document[];
}) {
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";
  const hasDocuments = documents.length > 0;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [query, setQuery] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeCitation, setActiveCitation] = useState<ResolvedCitation | null>(null);
  const [openTraceIndex, setOpenTraceIndex] = useState<number | null>(null);
  // Finding B (Low, UX audit): copy-to-clipboard confirmation + thumbs up/down feedback.
  // Feedback is tri-state locally (clicking the selected reaction deselects it) and is
  // persisted server-side via chatApi.submitFeedback (message_feedback feature) —
  // seeded from history's my_feedback on mount so it survives navigation/reload.
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Record<string, "up" | "down" | undefined>>({});
  const abortRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Abort in-progress stream on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.();
    };
  }, []);

  // Hydrate the conversation on mount and whenever the notebook changes — this is the
  // fix for "conversation vanishes on navigation": messages previously lived only in
  // this component's local state, with nothing reading conversations/messages back.
  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setIsLoadingHistory(true);
    chatApi
      .listMessages(notebookId)
      .then((history: ChatHistoryMessage[]) => {
        if (cancelled) return;
        // Guard against clobbering an in-flight ask the user fired off before history
        // finished loading: only apply history onto a still-empty conversation.
        setMessages((prev) =>
          prev.length === 0
            ? history.map((m) => ({
                role: m.role,
                content: m.content,
                citations: m.citations ?? [],
                messageId: m.id,
                claimChecks: m.claim_checks ?? null,
              }))
            : prev,
        );
        // Seed the feedback button state from each history message's own prior
        // my_feedback — otherwise the buttons reset to blank on every navigation even
        // though the rating was persisted server-side.
        setFeedback((prev) => {
          if (Object.keys(prev).length > 0) return prev;
          const seeded: Record<string, "up" | "down" | undefined> = {};
          for (const m of history) {
            if (m.my_feedback) seeded[m.id] = m.my_feedback;
          }
          return seeded;
        });
      })
      .catch(() => {
        /* history hydration failure isn't fatal — the panel still works for new asks */
      })
      .finally(() => {
        if (!cancelled) setIsLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [notebookId]);

  // Auto-scroll to bottom whenever messages update.
  // Use ?.() so jsdom (which doesn't implement scrollIntoView) doesn't throw in tests.
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages]);

  // Shared by the form's Enter/Ask-button submit AND the starter-question chips (Finding
  // A) — a chip click fills+submits in one step rather than leaving the user to press
  // Send, since the whole point is removing a step for someone facing a blank page.
  function submitQuery(rawQuery: string) {
    const q = rawQuery.trim();
    if (!q || isStreaming || !hasDocuments) return;

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
              messageId: response.message_id,
              weakEvidence: response.weak_evidence,
              claimChecks: response.claim_checks ?? null,
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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    submitQuery(query);
  }

  async function handleCopy(msg: ChatMessage) {
    if (!msg.messageId) return;
    await navigator.clipboard.writeText(msg.content);
    setCopiedMessageId(msg.messageId);
    setTimeout(() => {
      setCopiedMessageId((cur) => (cur === msg.messageId ? null : cur));
    }, 1500);
  }

  function handleFeedback(messageId: string, value: "up" | "down") {
    setFeedback((prev) => ({
      ...prev,
      // Tri-state: clicking the already-selected reaction deselects it.
      [messageId]: prev[messageId] === value ? undefined : value,
    }));
    // Fire-and-forget: the optimistic local state above is already applied, and a
    // network failure here shouldn't break the UI (no toast/error surface for this
    // round — see message_feedback feature notes).
    chatApi.submitFeedback(messageId, { rating: value }).catch(() => {});
  }

  const showCitationPanel = activeCitation !== null;

  return (
    <div className="flex flex-1 min-h-0">
      {/* Chat area */}
      <div className="flex flex-col flex-1 min-h-0">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && !isLoadingHistory && (
            <div className="text-center mt-8 space-y-3">
              <p className="text-muted text-sm">
                {hasDocuments
                  ? "Ask a question about the documents in this notebook."
                  : "This notebook has no documents yet. Attach one from the panel on the left, then come back and ask a question."}
              </p>
              {hasDocuments && (
                <div className="flex flex-wrap justify-center gap-2">
                  {STARTER_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => submitQuery(q)}
                      disabled={isStreaming}
                      className="text-xs border border-border rounded-full px-3 py-1.5 text-muted hover:border-accent hover:text-accent transition disabled:opacity-50"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {messages.map((msg, i) => {
            const isFinalAssistant = msg.role === "assistant" && msg.citations !== undefined;
            const canDebug = isFinalAssistant && isAdmin && msg.messageId !== undefined;
            // Finding B: copy/feedback controls visible to every role, not just admins.
            const canRate = isFinalAssistant && msg.messageId !== undefined;
            return (
              <div
                key={i}
                className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
              >
                {isFinalAssistant && msg.weakEvidence && (
                  // Reranker-score confidence gate indicator — StatusBadge's warning-pill
                  // convention (text-warning border-warning, text-xs, rounded-sm), since
                  // this is a degraded-confidence signal, not a hard error.
                  <span className="inline-flex items-center text-xs border rounded-sm px-2 py-0.5 text-warning border-warning mb-1">
                    Weak evidence
                  </span>
                )}
                <div
                  className={`max-w-[80%] rounded-lg px-4 py-3 text-sm ${
                    msg.role === "user"
                      ? "bg-accent text-white"
                      : "bg-surface border border-border text-text"
                  }`}
                >
                  {msg.role === "assistant" && msg.content === "" && isStreaming ? (
                    <span className="text-muted animate-pulse">●●●</span>
                  ) : isFinalAssistant ? (
                    <AnswerText
                      content={msg.content}
                      citations={msg.citations ?? []}
                      onCitationClick={setActiveCitation}
                      claimChecks={msg.claimChecks}
                    />
                  ) : (
                    <span className="whitespace-pre-wrap">{msg.content}</span>
                  )}
                </div>

                {isFinalAssistant && msg.claimChecks && msg.claimChecks.length > 0 && (
                  <ClaimCheckSummary checks={msg.claimChecks} />
                )}

                {canRate && (
                  <div className="max-w-[80%] w-full flex items-center gap-3 mt-1">
                    <button
                      type="button"
                      aria-label="Copy answer"
                      onClick={() => void handleCopy(msg)}
                      className="text-xs text-muted hover:text-text"
                    >
                      {copiedMessageId === msg.messageId ? "Copied" : "Copy"}
                    </button>
                    <button
                      type="button"
                      aria-label="Good response"
                      aria-pressed={feedback[msg.messageId!] === "up"}
                      onClick={() => handleFeedback(msg.messageId!, "up")}
                      className={`text-xs ${
                        feedback[msg.messageId!] === "up"
                          ? "text-accent"
                          : "text-muted hover:text-text"
                      }`}
                    >
                      👍
                    </button>
                    <button
                      type="button"
                      aria-label="Bad response"
                      aria-pressed={feedback[msg.messageId!] === "down"}
                      onClick={() => handleFeedback(msg.messageId!, "down")}
                      className={`text-xs ${
                        feedback[msg.messageId!] === "down"
                          ? "text-danger"
                          : "text-muted hover:text-text"
                      }`}
                    >
                      👎
                    </button>
                  </div>
                )}

                {canDebug && (
                  <div className="max-w-[80%] w-full">
                    <button
                      type="button"
                      onClick={() => setOpenTraceIndex(openTraceIndex === i ? null : i)}
                      className="text-xs text-muted hover:text-text mt-1 underline"
                    >
                      {openTraceIndex === i ? "Hide debug" : "Debug"}
                    </button>
                    {openTraceIndex === i && msg.messageId && (
                      <TraceDetails messageId={msg.messageId} />
                    )}
                  </div>
                )}
              </div>
            );
          })}

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
            disabled={isStreaming || !hasDocuments}
            placeholder={
              hasDocuments
                ? "Ask a question…"
                : "Attach a document to this notebook before asking a question"
            }
            className="flex-1 bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={isStreaming || !query.trim() || !hasDocuments}
            className="text-sm bg-accent text-white rounded-md px-4 py-2 hover:opacity-90 disabled:opacity-50"
          >
            {isStreaming ? "…" : "Ask"}
          </button>
        </form>
      </div>

      {/* Citation side panel: full-viewport overlay below `lg:` (there's no room for
          a third column on a phone), restored to the original `w-80` side column at
          `lg:`+. */}
      {showCitationPanel && activeCitation && (
        <div className="fixed inset-0 z-40 bg-bg lg:static lg:inset-auto lg:z-auto lg:w-80 shrink-0 border-l border-border flex flex-col overflow-hidden">
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
