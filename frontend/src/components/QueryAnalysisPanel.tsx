import type {
  SparseBooleanStep,
  SparseIndexStats,
  SparseMode,
  SparseQueryAnalysis,
} from "../types/retrieval";
import { TIPS, Tip } from "./Tip";

function Chip({
  children,
  struck = false,
  title,
}: {
  children: React.ReactNode;
  struck?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-block font-mono text-xs rounded-sm border border-border px-1.5 py-0.5 ${
        struck ? "text-muted line-through opacity-60" : "text-text"
      }`}
    >
      {children}
    </span>
  );
}

function Stage({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3">
      <span className="text-xs text-muted sm:w-36 shrink-0 pt-0.5">{label}</span>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

const fmt = (n: number, d = 3) => n.toFixed(d);

function StepChain({ steps }: { steps: SparseBooleanStep[] }) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs">
      {steps.map((s, i) => (
        <li key={i} className="flex items-center gap-1">
          {i > 0 && (
            <span className="text-muted" aria-hidden="true">
              →
            </span>
          )}
          <span className="rounded-sm border border-border px-1.5 py-0.5">
            <span className={s.op.includes("NOT") ? "text-danger" : "text-accent"}>{s.op}</span>{" "}
            <span className="font-mono">{s.term}</span>{" "}
            <span className="text-muted">(df {s.df})</span>{" "}
            <span className="font-mono text-text">= {s.result_size}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

export function IndexStatsBar({ stats }: { stats: SparseIndexStats }) {
  return (
    <p className="text-xs text-muted flex flex-wrap gap-x-4 gap-y-1" aria-label="Index statistics">
      <span>
        <Tip tip={TIPS.N}>N</Tip> = <span className="text-text font-mono">{stats.n_docs}</span>{" "}
        chunks
      </span>
      <span>
        <span className="text-text font-mono">{stats.vocabulary_size}</span>{" "}
        <Tip tip={TIPS.dictionary}>dictionary terms</Tip>
      </span>
      <span>
        <Tip tip={TIPS.avgPostings}>avg postings length</Tip>{" "}
        <span className="text-text font-mono">{fmt(stats.avg_postings_length, 1)}</span>
      </span>
      <span>
        <Tip tip={TIPS.zones}>zones</Tip>{" "}
        <span className="text-text font-mono">{stats.zones.join(", ") || "—"}</span>
      </span>
      <span>
        {stats.cached ? (
          <span className="text-success" title="Reused the in-memory index built by an earlier query">
            index cached
          </span>
        ) : (
          <>
            index built in{" "}
            <span className="text-text font-mono">{fmt(stats.build_ms, 1)} ms</span>
          </>
        )}
      </span>
      <span>
        query processed in <span className="text-text font-mono">{fmt(stats.query_ms, 2)} ms</span>
      </span>
    </p>
  );
}

export function QueryAnalysisPanel({
  analysis,
  mode,
}: {
  analysis: SparseQueryAnalysis;
  mode: SparseMode;
}) {
  const removed = new Set(analysis.stop_words_removed);
  const zones = Array.from(new Set(analysis.terms.flatMap((t) => Object.keys(t.postings)))).sort();
  const ranked = analysis.ranked;

  return (
    <details open className="bg-surface border border-border rounded-md p-4">
      <summary className="text-xs font-semibold uppercase tracking-wide text-muted cursor-pointer select-none">
        Query analysis
      </summary>
      <div className="mt-3 space-y-2">
        <Stage label="1. Tokenize (NFKC)">
          {analysis.raw_tokens.map((t, i) => (
            <Chip key={i}>{t}</Chip>
          ))}
        </Stage>
        <Stage label="2. Case-fold">
          {analysis.casefolded.map((t, i) => (
            <Chip key={i}>{t}</Chip>
          ))}
        </Stage>
        <Stage label={<Tip tip={TIPS.stopWords}>3. Stop words</Tip>}>
          {analysis.casefolded.map((t, i) => (
            <Chip
              key={i}
              struck={removed.has(t)}
              title={removed.has(t) ? "stop word removed" : undefined}
            >
              {t}
            </Chip>
          ))}
        </Stage>
        <Stage label={<Tip tip={TIPS.stem}>4. Porter stem</Tip>}>
          {analysis.stems.length === 0 ? (
            <span className="text-xs text-muted">no index terms left</span>
          ) : (
            analysis.stems.map((t, i) => <Chip key={i}>{t}</Chip>)
          )}
        </Stage>
      </div>

      {analysis.terms.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs" aria-label="Query term statistics">
            <thead className="text-muted text-left">
              <tr>
                <th className="font-normal py-1 pr-3">term</th>
                <th className="font-normal py-1 pr-3">
                  <Tip tip={TIPS.df}>df</Tip>
                </th>
                <th className="font-normal py-1 pr-3">
                  <Tip tip={TIPS.idf}>idf = log₁₀(N/df)</Tip>
                </th>
                {zones.map((z) => (
                  <th key={z} className="font-normal py-1 pr-3">
                    <Tip tip={TIPS.postings}>postings</Tip> · {z}
                  </th>
                ))}
                {mode === "ranked" && (
                  <th className="font-normal py-1 pr-3">
                    <Tip tip={TIPS.champions}>champions</Tip> ({zones.join(" / ")})
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {analysis.terms.map((t) => (
                <tr
                  key={t.term}
                  data-testid={`term-row-${t.term}`}
                  className={`border-t border-border ${t.eliminated ? "text-muted line-through" : ""}`}
                  title={t.eliminated ? "eliminated: idf at or below threshold" : undefined}
                >
                  <td className="py-1 pr-3 font-mono">
                    {t.term}
                    {t.surface.length > 0 && t.surface.join(", ") !== t.term && (
                      <span className="text-muted"> ← {t.surface.join(", ")}</span>
                    )}
                  </td>
                  <td className="py-1 pr-3 font-mono">{t.df}</td>
                  <td className="py-1 pr-3 font-mono">{fmt(t.idf)}</td>
                  {zones.map((z) => (
                    <td key={z} className="py-1 pr-3 font-mono">
                      {t.postings[z] ?? 0}
                    </td>
                  ))}
                  {mode === "ranked" && (
                    <td className="py-1 pr-3 font-mono">
                      {zones.map((z) => t.champions[z] ?? 0).join(" / ")}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {ranked && (
        <div className="mt-3 text-xs text-muted space-y-1">
          <p>
            {ranked.scheme === "bm25" ? (
              <>
                <Tip tip={TIPS.bm25}>Okapi BM25</Tip> per zone:{" "}
                <span className="font-mono text-text">
                  idf · (k₁+1)·tf / (k₁((1−b)+b·L/L̄)+tf)
                </span>
                , k₁=1.2, b=0.75
              </>
            ) : (
              <>
                SMART{" "}
                <Tip tip={TIPS.lncltc}>
                  <span className="font-mono text-text">lnc.ltc</span>
                </Tip>{" "}
                cosine per zone: doc
                (1+log tf)/‖d‖ × query (1+log tf)·idf/‖q‖
              </>
            )}
            ; score = Σ <Tip tip={TIPS.zoneWeights}>zone weight</Tip> × zone score (
            {Object.entries(ranked.zone_weights)
              .map(([z, w]) => `${z} ${w}`)
              .join(", ")}
            ); top-K selected with a heap.
          </p>
          <p>
            <Tip tip={TIPS.idfThreshold}>Index elimination</Tip> (idf ≤{" "}
            {ranked.idf_threshold.toFixed(2)}):{" "}
            {ranked.eliminated_terms.length > 0 ? (
              <span className="font-mono text-danger">
                dropped {ranked.eliminated_terms.join(", ")}
              </span>
            ) : (
              "nothing dropped"
            )}
            . Chunks with postings:{" "}
            <span className="font-mono text-text">{ranked.docs_with_postings}</span>
            {ranked.champion_candidates !== null && (
              <>
                {" "}
                · <Tip tip={TIPS.champions}>champion-list</Tip> candidates:{" "}
                <span className="font-mono text-text">{ranked.champion_candidates}</span>
              </>
            )}{" "}
            · scored: <span className="font-mono text-text">{ranked.docs_scored}</span>
          </p>
        </div>
      )}

      {analysis.boolean && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-muted">
            Parsed operators:{" "}
            <span className="font-mono text-text">
              {analysis.boolean.operators.length ? analysis.boolean.operators.join(" · ") : "—"}
            </span>
            . Postings intersected in increasing-df order, then AND NOT; clauses joined by OR.
          </p>
          {analysis.boolean.clauses.map((c, i) => (
            <div key={i} className="space-y-1">
              <p className="text-xs text-muted">
                Clause {i + 1}:{" "}
                <span className="font-mono">
                  {c.operands.map((o) => `${o.negated ? "NOT " : ""}${o.word}`).join(" AND ")}
                </span>
              </p>
              <StepChain steps={c.steps} />
            </div>
          ))}
          {analysis.boolean.union_steps.length > 1 && (
            <div className="space-y-1">
              <p className="text-xs text-muted">OR merge (union)</p>
              <StepChain steps={analysis.boolean.union_steps} />
            </div>
          )}
        </div>
      )}

      {analysis.phrase && (
        <div className="mt-3 space-y-1 text-xs text-muted">
          <p>
            Phrase terms @ positional offset:{" "}
            {analysis.phrase.terms.map((t) => (
              <span key={`${t.term}-${t.offset}`} className="font-mono text-text mr-2">
                {t.term}@+{t.offset}
              </span>
            ))}
          </p>
          {analysis.phrase.zones.map((z) => (
            <p key={z.zone}>
              <span className="font-mono text-text">{z.zone}</span>: postings ∩ →{" "}
              <span className="font-mono text-text">{z.candidates}</span> candidates → positional
              check → <span className="font-mono text-accent">{z.matched}</span> matches
            </p>
          ))}
          <p>
            Overall <span className="font-mono text-text">{analysis.phrase.candidates}</span>{" "}
            candidate chunks after intersection,{" "}
            <span className="font-mono text-accent">{analysis.phrase.matched}</span> contain the
            exact phrase.
          </p>
        </div>
      )}
    </details>
  );
}
