import { useState } from "react";
import type { SparseSearchResult } from "../types/retrieval";
import { HighlightedText } from "./HighlightedText";
import { TIPS, Tip } from "./Tip";

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
  const total = result.contributions.reduce((s, c) => s + c.weight, 0);
  // Each bar is the row's share of the total score, so the bars (and %s) sum to 100%.
  const share = (w: number) => (total > 0 ? (w / total) * 100 : 0);
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
            <span className="shrink-0 text-muted">
              score{" "}
              <span className="font-mono text-accent" title="Σ zone-weighted term contributions">
                {result.score.toFixed(4)}
              </span>
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
          <span key={m.zone} className="text-muted" title={TIPS.position}>
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
                <th className="font-normal py-1 pr-3">
                  <Tip tip={TIPS.tf}>tf</Tip>
                </th>
                <th className="font-normal py-1 pr-3">
                  <Tip tip={TIPS.idf}>idf</Tip>
                </th>
                <th className="font-normal py-1 pr-3">
                  <Tip tip={TIPS.weight}>weight</Tip>
                </th>
                <th className="font-normal py-1 w-1/3">
                  <Tip tip={TIPS.share}>share of score</Tip>
                </th>
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
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 rounded-sm bg-border">
                        <div
                          data-testid="contribution-bar"
                          className={`h-1.5 rounded-sm ${c.zone === "heading" ? "bg-warning" : "bg-accent"}`}
                          style={{ width: `${share(c.weight).toFixed(1)}%` }}
                        />
                      </div>
                      <span className="font-mono text-muted w-12 text-right">
                        {share(c.weight).toFixed(1)}%
                      </span>
                    </div>
                  </td>
                </tr>
              ))}
              <tr className="border-t border-border text-muted">
                <td className="py-1 pr-3" colSpan={4}>
                  Σ weights = score
                </td>
                <td className="py-1 pr-3 font-mono text-text">{total.toFixed(4)}</td>
                <td className="py-1 font-mono text-right">{total > 0 ? "100%" : "—"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}
