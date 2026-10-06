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
import { Markdown, type Annotation } from "./Markdown";

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

function TraceDetails({
  messageId,
  claimChecks,
}: {
  messageId: string;
  claimChecks?: ClaimCheck[] | null;
}) {
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
                [{hit.index}] doc {hit.document_id.slice(0, 8)}… · chars {hit.char_start}–
                {hit.char_end}
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
      {claimChecks && claimChecks.length > 0 && (
        <div>
          <p className="font-medium text-muted mb-1">Claim checks ({claimChecks.length})</p>
          <table className="border-collapse text-[11px] w-full" aria-label="Claim checks">
            <thead>
              <tr className="text-left">
                <th className="pr-3 font-medium">sentence</th>
                <th className="pr-3 font-medium">cites</th>
                <th className="pr-3 font-medium text-right">lexical</th>
                <th className="pr-3 font-medium text-right">semantic</th>
                <th className="pr-3 font-medium text-right">score</th>
                <th className="font-medium">status</th>
              </tr>
            </thead>
            <tbody>
              {claimChecks.map((c, i) => (
                <tr key={i} className="align-top text-muted">
                  <td className="pr-3 py-0.5">{c.sentence}</td>
                  <td className="pr-3 font-mono whitespace-nowrap">
                    {c.citations.map((n) => `[${n}]`).join("") || "—"}
                    {c.citations_inherited && " (para)"}
                  </td>
                  <td className="pr-3 font-mono text-right">{fmtScore(c.lexical)}</td>
                  <td className="pr-3 font-mono text-right">{fmtScore(c.semantic)}</td>
                  <td className="pr-3 font-mono text-right">{fmtScore(c.score)}</td>
                  <td className={c.status === "weak" ? "text-warning" : ""}>{c.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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

// Answers render as markdown (D5) with [n] markers as clickable citation buttons.
// Every checked sentence from the per-sentence citation check carries a title tooltip
// with its lexical/semantic/combined scores and the [n] it was checked against.
// Supported = visually quiet (no underline); weak = amber underline + ⚠; uncited = grey
// dotted underline.
function fmtScore(v: number | null): string {
  return v === null ? "n/a" : v.toFixed(2);
}

function markerList(check: ClaimCheck): string {
  return check.citations.map((n) => `[${n}]`).join("");
}

function claimTooltip(check: ClaimCheck): string {
  if (check.status === "uncited") {
    return "Uncited: no source marker anywhere in this paragraph.";
  }
  const label = check.status === "supported" ? "Supported" : "Weak support";
  const source = check.citations_inherited
    ? `${markerList(check)} (covered by ${markerList(check)} at the end of the paragraph)`
    : markerList(check);
  return `${label} by ${source} — lexical ${fmtScore(check.lexical)} · semantic ${fmtScore(check.semantic)} · combined ${fmtScore(check.score)}`;
}

const CLAIM_CLASS: Record<ClaimCheck["status"], string> = {
  supported: "cursor-help",
  weak: "cursor-help underline decoration-warning decoration-2 underline-offset-2",
  uncited: "cursor-help underline decoration-dotted decoration-muted underline-offset-2",
};

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
  // Locate each checked sentence (backend sentences are verbatim substrings of the raw
  // answer, markdown syntax included) in order; the Markdown renderer overlays these
  // raw-text ranges onto its rendered output, so a sentence containing `**bold**` or a
  // list item is still wrapped as one claim span.
  const annotations: Annotation<ClaimCheck>[] = [];
  let cursor = 0;
  for (const check of claimChecks ?? []) {
    const at = content.indexOf(check.sentence, cursor);
    if (at === -1) continue;
    annotations.push({ start: at, end: at + check.sentence.length, data: check });
    cursor = at + check.sentence.length;
  }

  return (
    <Markdown
      content={content}
      renderCitation={(marker, raw, key) => (
        <CitationMarker
          key={key}
          marker={marker}
          raw={raw}
          citations={citations}
          onCitationClick={onCitationClick}
        />
      )}
      annotations={annotations}
      renderAnnotation={(check, children, key, isEnd) => (
        <span
          key={key}
          data-claim-status={check.status}
          title={claimTooltip(check)}
          className={CLAIM_CLASS[check.status]}
        >
          {children}
          {isEnd && check.status === "weak" && (
            <span className="text-warning text-xs ml-0.5" aria-hidden="true">
              ⚠
            </span>
          )}
        </span>
      )}
    />
  );
}

// Shared look for the per-answer action row (Copy / 👍 / 👎 / Debug).
const ACTION_BTN =
  "text-xs text-muted hover:text-text hover:bg-surface rounded-md px-2 py-1 transition";

function ClaimCheckSummary({ checks }: { checks: ClaimCheck[] }) {
  const count = (s: ClaimCheck["status"]) => checks.filter((c) => c.status === s).length;
  return (
    <p className="text-xs text-muted mt-1.5 px-2" data-testid="claim-check-summary">
      Claim check: {count("supported")} supported · {count("weak")} weak ·{" "}
      {count("uncited")} uncited
    </p>
  );
}

function CitationMarker({
  marker,
  raw,
  citations,
  onCitationClick,
}: {
  marker: number;
  raw: string;
  citations: ResolvedCitation[];
  onCitationClick: (c: ResolvedCitation) => void;
}) {
  const citation = citations.find((c) => c.marker === marker);
  return citation ? (
    <button
      type="button"
      onClick={() => onCitationClick(citation)}
      className="font-mono text-accent text-xs hover:underline align-super px-0.5"
    >
      [{marker}]
    </button>
  ) : (
    <span className="font-mono text-muted text-xs">{raw}</span>
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
  const listRef = useRef<HTMLDivElement>(null);

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

  // Auto-scroll the message list (only it — never the page/shell, which
  // scrollIntoView would also move) to the newest message whenever messages update.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
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
        <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto p-4 space-y-5">
          {messages.length === 0 && !isLoadingHistory && (
            <div className="text-center mt-12 max-w-md mx-auto space-y-4">
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
                  ) : msg.role === "assistant" ? (
                    // Still streaming: render markdown progressively (an unclosed `**`
                    // stays literal until its closer arrives); markers aren't resolved yet.
                    <Markdown content={msg.content} />
                  ) : (
                    <span className="whitespace-pre-wrap">{msg.content}</span>
                  )}
                </div>

                {isFinalAssistant && msg.claimChecks && msg.claimChecks.length > 0 && (
                  <ClaimCheckSummary checks={msg.claimChecks} />
                )}

                {canRate && (
                  <div className="max-w-[80%] w-full flex items-center gap-1 mt-0.5 text-xs">
                    <button
                      type="button"
                      aria-label="Copy answer"
                      onClick={() => void handleCopy(msg)}
                      className={ACTION_BTN}
                    >
                      {copiedMessageId === msg.messageId ? "Copied" : "Copy"}
                    </button>
                    <button
                      type="button"
                      aria-label="Good response"
                      aria-pressed={feedback[msg.messageId!] === "up"}
                      onClick={() => handleFeedback(msg.messageId!, "up")}
                      className={`${ACTION_BTN} ${
                        feedback[msg.messageId!] === "up"
                          ? "bg-surface"
                          : "grayscale opacity-60 hover:opacity-100 hover:grayscale-0"
                      }`}
                    >
                      👍
                    </button>
                    <button
                      type="button"
                      aria-label="Bad response"
                      aria-pressed={feedback[msg.messageId!] === "down"}
                      onClick={() => handleFeedback(msg.messageId!, "down")}
                      className={`${ACTION_BTN} ${
                        feedback[msg.messageId!] === "down"
                          ? "bg-surface"
                          : "grayscale opacity-60 hover:opacity-100 hover:grayscale-0"
                      }`}
                    >
                      👎
                    </button>
                    {canDebug && (
                      <>
                        <span className="text-border mx-1" aria-hidden="true">
                          |
                        </span>
                        <button
                          type="button"
                          onClick={() => setOpenTraceIndex(openTraceIndex === i ? null : i)}
                          aria-expanded={openTraceIndex === i}
                          className={ACTION_BTN}
                        >
                          {openTraceIndex === i ? "Hide debug" : "Debug"}
                        </button>
                      </>
                    )}
                  </div>
                )}

                {canDebug && openTraceIndex === i && (
                  <div className="max-w-[80%] w-full">
                    {msg.messageId && (
                      <TraceDetails messageId={msg.messageId} claimChecks={msg.claimChecks} />
                    )}
                  </div>
                )}
              </div>
            );
          })}

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
