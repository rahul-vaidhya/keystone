import { useState } from "react";
import type { SparseSearchResult } from "../types/retrieval";
import { HighlightedText } from "./HighlightedText";

function pageLabel(r: SparseSearchResult): string | null {
  if (r.page_start === null) return null;
  return r.page_end === null || r.page_end === r.page_start
    ? `p. ${r.page_start}`
    : `pp. ${r.page_start}–${r.page_end}`;
}

export function SparseResultCard({
  result,
  onOpen,
}: {
  result: SparseSearchResult;
  onOpen: (r: SparseSearchResult) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const maxWeight = Math.max(0, ...result.contributions.map((c) => c.weight));
  const total = result.contributions.reduce((s, c) => s + c.weight, 0);
  const page = pageLabel(result);

  return (
    <article className="bg-surface border border-border rounded-md hover:border-accent transition">
      <button
        type="button"
        onClick={() => onOpen(result)}
        className="w-full text-left p-3 space-y-1"
        aria-label={`Open source #${result.rank}`}
      >
        <div className="flex items-center gap-2 text-xs">
          <span className="font-mono text-muted">#{result.rank}</span>
          <span className="text-sm font-medium truncate flex-1" title={result.document_title ?? ""}>
            {result.document_title ?? "Document"}
          </span>
          {page && <span className="text-muted shrink-0">{page}</span>}
          {result.score !== null && (
            <span className="font-mono text-accent shrink-0" title="score">
              {result.score.toFixed(4)}
            </span>
          )}
        </div>
        {result.heading && <p className="text-xs text-muted truncate">§ {result.heading}</p>}
        <p className="text-sm text-muted line-clamp-4 whitespace-pre-wrap">
          <HighlightedText text={result.snippet} words={result.highlights} />
        </p>
      </button>

      <div className="px-3 pb-2 flex flex-wrap items-center gap-2 text-xs">
        {result.matched_terms.map((t) => (
          <span
            key={t}
            className="font-mono rounded-sm border border-border px-1.5 py-0.5 text-muted"
          >
            {t}
          </span>
        ))}
        {result.phrase_matches.map((m) => (
          <span key={m.zone} className="text-muted">
            phrase in {m.zone} at position{m.positions.length > 1 ? "s" : ""}{" "}
            <span className="font-mono text-text">{m.positions.join(", ")}</span>
          </span>
        ))}
        {result.contributions.length > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="ml-auto text-accent hover:underline"
          >
            {expanded ? "Hide breakdown" : "Why this score?"}
          </button>
        )}
      </div>

      {expanded && (
        <div className="px-3 pb-3 overflow-x-auto">
          <table className="w-full text-xs" aria-label="Term contributions">
            <thead className="text-muted text-left">
              <tr>
                <th className="font-normal py-1 pr-3">term</th>
                <th className="font-normal py-1 pr-3">zone</th>
                <th className="font-normal py-1 pr-3">tf</th>
                <th className="font-normal py-1 pr-3">idf</th>
                <th className="font-normal py-1 pr-3">weight</th>
                <th className="font-normal py-1 w-1/3">share</th>
              </tr>
            </thead>
            <tbody>
              {result.contributions.map((c, i) => (
                <tr key={`${c.term}-${c.zone}-${i}`} className="border-t border-border">
                  <td className="py-1 pr-3 font-mono">{c.term}</td>
                  <td className={`py-1 pr-3 ${c.zone === "heading" ? "text-warning" : "text-muted"}`}>
                    {c.zone}
                  </td>
                  <td className="py-1 pr-3 font-mono">{c.tf}</td>
                  <td className="py-1 pr-3 font-mono">{c.idf.toFixed(3)}</td>
                  <td className="py-1 pr-3 font-mono">{c.weight.toFixed(4)}</td>
                  <td className="py-1">
                    <div className="h-1.5 rounded-sm bg-border">
                      <div
                        data-testid="contribution-bar"
                        className={`h-1.5 rounded-sm ${c.zone === "heading" ? "bg-warning" : "bg-accent"}`}
                        style={{ width: `${maxWeight > 0 ? (c.weight / maxWeight) * 100 : 0}%` }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
              <tr className="border-t border-border text-muted">
                <td className="py-1 pr-3" colSpan={4}>
                  Σ weights = score
                </td>
                <td className="py-1 pr-3 font-mono text-text">{total.toFixed(4)}</td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}
