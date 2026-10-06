import type { SparseScheme } from "../types/retrieval";
import { TIPS } from "./Tip";

export type RankedOptions = {
  scheme: SparseScheme;
  useChampions: boolean;
  idfThreshold: number;
  headingWeight: number;
  bodyWeight: number;
};

export const DEFAULT_RANKED_OPTIONS: RankedOptions = {
  scheme: "bm25",
  useChampions: false,
  idfThreshold: 0,
  headingWeight: 2,
  bodyWeight: 1,
};

function Slider({
  label,
  tip,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  tip: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted" title={tip}>
      <span className="whitespace-nowrap underline decoration-dotted underline-offset-2 cursor-help">
        {label}
      </span>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-24 accent-[var(--accent)]"
      />
      <span className="font-mono text-text w-8 text-right">{value.toFixed(2)}</span>
    </label>
  );
}

export function SparseOptions({
  value,
  onChange,
}: {
  value: RankedOptions;
  onChange: (next: RankedOptions) => void;
}) {
  const set = <K extends keyof RankedOptions>(key: K, v: RankedOptions[K]) =>
    onChange({ ...value, [key]: v });

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2" aria-label="Ranked retrieval options">
      <div role="radiogroup" aria-label="Scoring scheme" className="flex rounded-md border border-border overflow-hidden text-xs">
        {(
          [
            ["tfidf", "tf-idf (lnc.ltc)"],
            ["bm25", "BM25"],
          ] as const
        ).map(([scheme, label]) => (
          <button
            key={scheme}
            type="button"
            role="radio"
            title={scheme === "bm25" ? TIPS.bm25 : TIPS.lncltc}
            aria-checked={value.scheme === scheme}
            onClick={() => set("scheme", scheme)}
            className={`px-3 py-1 transition ${
              value.scheme === scheme ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-xs text-muted cursor-pointer" title={TIPS.champions}>
        <input
          type="checkbox"
          checked={value.useChampions}
          onChange={(e) => set("useChampions", e.target.checked)}
          className="accent-[var(--accent)]"
        />
        Champion lists
      </label>
      <Slider
        label="idf threshold"
        tip={TIPS.idfThreshold}
        value={value.idfThreshold}
        min={0}
        max={1.5}
        step={0.05}
        onChange={(v) => set("idfThreshold", v)}
      />
      <Slider
        label="heading weight"
        tip={TIPS.zoneWeights}
        value={value.headingWeight}
        min={0}
        max={5}
        step={0.5}
        onChange={(v) => set("headingWeight", v)}
      />
      <Slider
        label="body weight"
        tip={TIPS.zoneWeights}
        value={value.bodyWeight}
        min={0}
        max={5}
        step={0.5}
        onChange={(v) => set("bodyWeight", v)}
      />
    </div>
  );
}
