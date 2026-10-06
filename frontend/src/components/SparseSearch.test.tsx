import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { SparseQueryAnalysis, SparseSearchResult } from "../types/retrieval";
import { HighlightedText } from "./HighlightedText";
import { IndexStatsBar, QueryAnalysisPanel } from "./QueryAnalysisPanel";
import { SparseResultCard } from "./SparseResultCard";
import { DEFAULT_RANKED_OPTIONS, SparseOptions } from "./SparseOptions";

function makeAnalysis(overrides: Partial<SparseQueryAnalysis> = {}): SparseQueryAnalysis {
  return {
    raw_tokens: ["The", "Octet", "rules"],
    casefolded: ["the", "octet", "rules"],
    stop_words_removed: ["the"],
    kept_tokens: ["octet", "rules"],
    stems: ["octet", "rule"],
    terms: [
      {
        term: "octet",
        surface: ["octet"],
        query_tf: 1,
        df: 4,
        idf: 1.44,
        postings: { body: 4, heading: 1 },
        champions: { body: 4, heading: 1 },
        eliminated: false,
      },
      {
        term: "rule",
        surface: ["rules"],
        query_tf: 1,
        df: 60,
        idf: 0.26,
        postings: { body: 60, heading: 2 },
        champions: { body: 50, heading: 2 },
        eliminated: true,
      },
    ],
    ranked: {
      scheme: "bm25",
      zone_weights: { heading: 2, body: 1 },
      use_champions: false,
      idf_threshold: 0.3,
      active_terms: ["octet"],
      eliminated_terms: ["rule"],
      docs_with_postings: 4,
      champion_candidates: null,
      docs_scored: 4,
    },
    boolean: null,
    phrase: null,
    ...overrides,
  };
}

function makeResult(overrides: Partial<SparseSearchResult> = {}): SparseSearchResult {
  return {
    rank: 1,
    chunk_id: "chunk-1",
    document_id: "doc-1",
    document_title: "kech104.pdf",
    heading: "Kössel-Lewis Approach",
    content: "### Page 2\nThe octet rule says atoms gain an Octet.",
    snippet: "### Page 2\nThe octet rule says atoms gain an Octet.",
    char_start: 0,
    char_end: 50,
    page_start: 2,
    page_end: 2,
    score: 3.5,
    contributions: [
      { term: "octet", zone: "body", tf: 2, idf: 1.44, weight: 2.5 },
      { term: "octet", zone: "heading", tf: 1, idf: 1.44, weight: 1.0 },
    ],
    matched_terms: ["octet"],
    highlights: ["octet", "Octet"],
    phrase_matches: [],
    ...overrides,
  };
}

describe("HighlightedText", () => {
  it("marks whole-word matches and strips parser page markers", () => {
    const { container } = render(
      <HighlightedText text={"### Page 2\nThe octet rule; Octet octets"} words={["octet", "Octet"]} />,
    );
    const marks = container.querySelectorAll("mark");
    expect(Array.from(marks).map((m) => m.textContent)).toEqual(["octet", "Octet"]);
    expect(container.textContent).not.toContain("Page 2");
  });
});

describe("QueryAnalysisPanel", () => {
  it("shows the pipeline, term statistics and struck-through eliminated terms", () => {
    render(<QueryAnalysisPanel analysis={makeAnalysis()} mode="ranked" />);
    expect(screen.getByText("1. Tokenize (NFKC)")).toBeInTheDocument();
    expect(screen.getByTitle("stop word removed")).toHaveTextContent("the");
    expect(screen.getByTestId("term-row-rule")).toHaveClass("line-through");
    expect(screen.getByTestId("term-row-octet")).not.toHaveClass("line-through");
    expect(screen.getByText(/dropped rule/)).toBeInTheDocument();
    expect(screen.getByText(/Okapi BM25/)).toBeInTheDocument();
  });

  it("renders the Boolean merge steps in processing order", () => {
    const analysis = makeAnalysis({
      ranked: null,
      boolean: {
        operators: ["AND", "NOT"],
        clauses: [
          {
            operands: [
              { word: "hydrogen", terms: ["hydrogen"], negated: false },
              { word: "bond", terms: ["bond"], negated: false },
              { word: "covalent", terms: ["coval"], negated: true },
            ],
            steps: [
              { op: "START", term: "hydrogen", df: 12, result_size: 12 },
              { op: "AND", term: "bond", df: 40, result_size: 9 },
              { op: "AND NOT", term: "coval", df: 20, result_size: 4 },
            ],
            result_size: 4,
          },
        ],
        union_steps: [{ op: "OR", term: "clause 1", df: 4, result_size: 4 }],
      },
    });
    render(<QueryAnalysisPanel analysis={analysis} mode="boolean" />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toContain("START hydrogen (df 12) = 12");
    expect(items[1]).toContain("AND bond (df 40) = 9");
    expect(items[2]).toContain("AND NOT coval (df 20) = 4");
  });

  it("renders phrase candidates vs positional matches", () => {
    const analysis = makeAnalysis({
      ranked: null,
      phrase: {
        phrase: "lattice enthalpy",
        terms: [
          { term: "lattic", offset: 0 },
          { term: "enthalpi", offset: 1 },
        ],
        zones: [{ zone: "body", postings: { lattic: 9, enthalpi: 7 }, candidates: 5, matched: 3 }],
        candidates: 5,
        matched: 3,
      },
    });
    render(<QueryAnalysisPanel analysis={analysis} mode="phrase" />);
    expect(screen.getByText("lattic@+0")).toBeInTheDocument();
    expect(screen.getByText(/contain the/)).toHaveTextContent("3 contain the exact phrase");
  });

  it("IndexStatsBar shows N, vocabulary and timing", () => {
    render(
      <IndexStatsBar
        stats={{
          n_docs: 110,
          vocabulary_size: 2048,
          avg_postings_length: 3.21,
          zones: ["body", "heading"],
          champion_r: 50,
          cached: true,
          build_ms: 0,
          query_ms: 1.234,
          total_ms: 9,
        }}
      />,
    );
    const bar = screen.getByLabelText("Index statistics");
    expect(bar).toHaveTextContent("N = 110 chunks");
    expect(bar).toHaveTextContent("2048 dictionary terms");
    expect(bar).toHaveTextContent("index cached");
    expect(bar).toHaveTextContent("1.23 ms");
  });
});

describe("SparseResultCard", () => {
  it("shows score, page, and an expandable contribution table that sums to the score", () => {
    const onOpen = vi.fn();
    render(<SparseResultCard result={makeResult()} onOpen={onOpen} />);
    expect(screen.getByText("3.5000")).toBeInTheDocument();
    expect(screen.getByText("p. 2")).toBeInTheDocument();
    expect(screen.queryByLabelText("Term contributions")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Why this score?" }));
    const table = screen.getByLabelText("Term contributions");
    expect(within(table).getAllByTestId("contribution-bar")).toHaveLength(2);
    expect(within(table).getAllByTestId("contribution-bar")[0]).toHaveStyle({ width: "71.4%" });
    expect(within(table).getByText("71.4%")).toBeInTheDocument();
    expect(within(table).getByText("28.6%")).toBeInTheDocument();
    expect(within(table).getByText("Σ weights = score").parentElement).toHaveTextContent("3.5000");

    fireEvent.click(screen.getByRole("button", { name: "Open source #1" }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ chunk_id: "chunk-1" }));
  });

  it("shows phrase positions and no score for unranked modes", () => {
    render(
      <SparseResultCard
        result={makeResult({
          score: null,
          contributions: [],
          phrase_matches: [{ zone: "body", positions: [4, 19] }],
        })}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText(/phrase in body at positions/)).toHaveTextContent("4, 19");
    expect(screen.queryByRole("button", { name: "Why this score?" })).not.toBeInTheDocument();
  });
});

describe("SparseOptions", () => {
  it("toggles scheme and champion lists", () => {
    const onChange = vi.fn();
    render(<SparseOptions value={DEFAULT_RANKED_OPTIONS} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "tf-idf (lnc.ltc)" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_RANKED_OPTIONS, scheme: "tfidf" });
    fireEvent.click(screen.getByLabelText("Champion lists"));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_RANKED_OPTIONS, useChampions: true });
    fireEvent.change(screen.getByLabelText("idf threshold"), { target: { value: "0.5" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_RANKED_OPTIONS, idfThreshold: 0.5 });
  });
});
