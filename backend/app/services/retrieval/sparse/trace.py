"""Explain traces for the sparse IR core — the same algorithms as ``scoring``, but
returning every intermediate step so the Search page can SHOW them (IIR = Manning,
Raghavan & Schütze, *Introduction to Information Retrieval*):

- ``query_pipeline`` — the analysis chain a query goes through (IIR §2.2): raw tokens ->
  case folding -> stop-word removal -> Porter stems.
- ``term_stats`` — per query term: df, idf (IIR §6.2.1), postings-list length per zone
  (zone index, IIR §6.1), champion-list size (IIR §7.1.3), and whether index elimination
  (IIR §7.1.2) dropped it.
- ``ranked_search_trace`` — ``scoring.search`` plus the candidate-pool sizes (docs that
  have any postings vs. docs the champion lists keep).
- ``boolean_search_trace`` — ``scoring.boolean_search`` with the parsed clauses and every
  merge step (IIR §1.3: positive terms processed in increasing-df order, then AND NOT,
  then OR across clauses) with the intermediate result size after each step.
- ``phrase_search_trace`` — ``scoring.phrase_search`` with candidates after postings
  intersection vs. docs surviving the positional check (IIR §2.4.2), plus match positions.
- ``matching_surface_forms`` / ``phrase_occurrence_spans`` / ``snippet_around`` —
  highlight helpers.
- ``validate_boolean_query`` — rejects dangling operators / parentheses up front.

The existing ``scoring`` APIs are untouched; the trace functions return exactly the same
result sets (asserted by tests). Pure module: no DB, no app imports.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from app.services.retrieval.sparse.index import InvertedIndex
from app.services.retrieval.sparse.scoring import (
    SparseHit,
    _active_zones,
    _champion_candidates,
    _query_terms,
    and_not,
    intersect,
    search,
    union,
)
from app.services.retrieval.sparse.text import (
    STOP_WORDS,
    analyze,
    analyze_with_positions,
    stem,
    tokenize,
)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
BOOLEAN_OPERATORS = frozenset({"AND", "OR", "NOT"})


# ---- query analysis --------------------------------------------------------------------


@dataclass(frozen=True)
class QueryPipeline:
    raw_tokens: list[str]
    casefolded: list[str]
    stop_words_removed: list[str]
    kept_tokens: list[str]
    stems: list[str]


def query_pipeline(text: str) -> QueryPipeline:
    """The analysis chain of ``text.analyze``, one stage at a time."""
    folded = tokenize(text)
    raw = _TOKEN_RE.findall(unicodedata.normalize("NFKC", text))
    if len(raw) != len(folded):  # case folding changed token boundaries (very rare)
        raw = list(folded)
    removed = [t for t in folded if t in STOP_WORDS]
    kept = [t for t in folded if t not in STOP_WORDS]
    return QueryPipeline(
        raw_tokens=raw,
        casefolded=folded,
        stop_words_removed=removed,
        kept_tokens=kept,
        stems=[stem(t) for t in kept],
    )


@dataclass(frozen=True)
class TermStat:
    term: str
    surface: list[str]
    query_tf: int
    df: int
    idf: float
    postings: dict[str, int]
    champions: dict[str, int]
    eliminated: bool


def term_stats(
    index: InvertedIndex, pipeline: QueryPipeline, *, idf_threshold: float | None = None
) -> list[TermStat]:
    """Dictionary statistics for each distinct query stem (first-occurrence order).
    ``eliminated`` mirrors ``scoring._query_terms``: idf <= ``idf_threshold`` (never set
    when ``idf_threshold`` is ``None``, i.e. for Boolean/phrase queries)."""
    counts = Counter(pipeline.stems)
    surface: dict[str, list[str]] = {}
    for tok, st in zip(pipeline.kept_tokens, pipeline.stems, strict=True):
        forms = surface.setdefault(st, [])
        if tok not in forms:
            forms.append(tok)
    out: list[TermStat] = []
    for term in dict.fromkeys(pipeline.stems):
        idf = index.idf(term)
        out.append(
            TermStat(
                term=term,
                surface=surface[term],
                query_tf=counts[term],
                df=index.df(term),
                idf=idf,
                postings={z: len(index.postings(term, z)) for z in index.zones},
                champions={z: len(index.champions(term, z)) for z in index.zones},
                eliminated=idf_threshold is not None and idf <= idf_threshold,
            )
        )
    return out


def index_avg_postings_length(index: InvertedIndex) -> float:
    """Mean document-level postings-list length over the dictionary (= mean df)."""
    terms = index.terms()
    if not terms:
        return 0.0
    return sum(len(index.doc_postings(t)) for t in terms) / len(terms)


# ---- ranked ----------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedTrace:
    active_terms: list[str]
    eliminated_terms: list[str]
    zones: dict[str, float]
    docs_with_postings: int
    champion_candidates: int | None
    docs_scored: int


def ranked_search_trace(
    index: InvertedIndex,
    query: str,
    *,
    k: int,
    scheme: Literal["tfidf", "bm25"],
    zone_weights: dict[str, float] | None,
    use_champions: bool,
    idf_threshold: float,
) -> tuple[list[SparseHit], RankedTrace]:
    """``scoring.search(..., explain=True)`` plus candidate-pool accounting: how many docs
    have postings for the surviving terms, how many the champion lists keep, and how many
    accumulators ended up scored."""
    hits = search(
        index,
        query,
        k=k,
        scheme=scheme,
        zone_weights=zone_weights,
        use_champions=use_champions,
        idf_threshold=idf_threshold,
        explain=True,
    )
    terms = _query_terms(index, query, idf_threshold)
    zones = _active_zones(index, zone_weights)
    all_terms = list(dict.fromkeys(analyze(query)))
    pool: set[str] = set()
    for term in terms:
        for zone in zones:
            pool.update(doc_id for doc_id, _ in index.postings(term, zone))
    champs = _champion_candidates(index, terms, zones) if use_champions else None
    scored = pool & champs if champs is not None else pool
    return hits, RankedTrace(
        active_terms=list(terms),
        eliminated_terms=[t for t in all_terms if t not in terms],
        zones=zones,
        docs_with_postings=len(pool),
        champion_candidates=len(champs) if champs is not None else None,
        docs_scored=len(scored) if terms and zones else 0,
    )


# ---- Boolean ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BooleanOperand:
    word: str
    terms: list[str]
    negated: bool


@dataclass(frozen=True)
class BooleanStep:
    op: str  # "START" | "AND" | "AND NOT" | "ALL" | "OR"
    term: str
    df: int
    result_size: int


@dataclass
class BooleanClauseTrace:
    operands: list[BooleanOperand]
    steps: list[BooleanStep] = field(default_factory=list)
    result_size: int = 0


@dataclass(frozen=True)
class BooleanTrace:
    operators: list[str]
    clauses: list[BooleanClauseTrace]
    union_steps: list[BooleanStep]
    result: list[str]


class MalformedBooleanQuery(ValueError):
    """A Boolean query the left-to-right parser can't interpret unambiguously (dangling
    operator, parentheses, no terms). Mapped to HTTP 422 with ``str(exc)`` as detail."""


BOOLEAN_SYNTAX_HELP = (
    "Use uppercase AND / OR / NOT between words, with a term on both sides of AND/OR "
    "(e.g. hydrogen AND bond NOT covalent). Parentheses aren't supported — operators "
    "are evaluated left to right, AND binding tighter than OR."
)


def validate_boolean_query(query: str) -> None:
    """Reject queries ``_parse_boolean`` would otherwise silently reinterpret. Raises
    ``MalformedBooleanQuery`` with a human-readable reason + the syntax help."""

    def fail(reason: str) -> None:
        raise MalformedBooleanQuery(f"{reason} {BOOLEAN_SYNTAX_HELP}")

    if "(" in query or ")" in query:
        fail("Grouping with ( ) isn't available.")
    tokens = query.split()
    if not any(t not in BOOLEAN_OPERATORS for t in tokens):
        fail("The Boolean query has no search terms.")
    if tokens[0] in {"AND", "OR"}:
        fail(f"The query can't start with {tokens[0]}.")
    if tokens[-1] in BOOLEAN_OPERATORS:
        fail(f"The query can't end with {tokens[-1]}.")
    for prev, cur in zip(tokens, tokens[1:], strict=False):
        if prev in BOOLEAN_OPERATORS and cur in {"AND", "OR"}:
            fail(f"'{prev} {cur}' needs a term between the operators.")


def _parse_boolean(query: str) -> tuple[list[list[BooleanOperand]], list[str]]:
    clauses: list[list[BooleanOperand]] = [[]]
    operators: list[str] = []
    negate = False
    pending_and = False
    for raw in query.split():
        if raw == "OR":
            clauses.append([])
            operators.append("OR")
            negate = False
            pending_and = False
        elif raw == "AND":
            operators.append("AND")
            pending_and = True
            continue
        elif raw == "NOT":
            operators.append("NOT")
            negate = not negate
        else:
            terms = analyze(raw)
            if terms:
                if clauses[-1] and not pending_and and not negate:
                    operators.append("AND (implicit)")
                clauses[-1].append(BooleanOperand(word=raw, terms=terms, negated=negate))
            negate = False
            pending_and = False
    return clauses, operators


def boolean_search_trace(index: InvertedIndex, query: str) -> BooleanTrace:
    """Same semantics and result as ``scoring.boolean_search``, with every merge step."""
    clauses, operators = _parse_boolean(query)
    traces: list[BooleanClauseTrace] = []
    union_steps: list[BooleanStep] = []
    result: list[str] = []
    for clause in clauses:
        if not clause:
            continue
        trace = BooleanClauseTrace(operands=clause)
        positives = [
            (t, index.doc_postings(t)) for op in clause if not op.negated for t in op.terms
        ]
        if positives:
            ordered = sorted(positives, key=lambda tp: len(tp[1]))  # increasing df
            first_term, matched = ordered[0]
            trace.steps.append(BooleanStep("START", first_term, len(matched), len(matched)))
            for term, plist in ordered[1:]:
                if not matched:
                    break
                matched = intersect(matched, plist)
                trace.steps.append(BooleanStep("AND", term, len(plist), len(matched)))
        else:
            matched = list(index.doc_ids)
            trace.steps.append(BooleanStep("ALL", "*", index.n_docs, len(matched)))
        for op in clause:
            if op.negated:
                lists = sorted((index.doc_postings(t) for t in op.terms), key=len)
                neg = lists[0]
                for plist in lists[1:]:
                    if not neg:
                        break
                    neg = intersect(neg, plist)
                matched = and_not(matched, neg)
                trace.steps.append(
                    BooleanStep("AND NOT", " ".join(op.terms), len(neg), len(matched))
                )
        trace.result_size = len(matched)
        traces.append(trace)
        result = union(result, matched)
        union_steps.append(BooleanStep("OR", f"clause {len(traces)}", len(matched), len(result)))
    return BooleanTrace(operators=operators, clauses=traces, union_steps=union_steps, result=result)


# ---- phrase ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PhraseZoneTrace:
    zone: str
    postings: dict[str, int]
    candidates: int
    matched: int


@dataclass(frozen=True)
class PhraseTrace:
    offsets: list[tuple[str, int]]
    zones: list[PhraseZoneTrace]
    candidates: int
    matches: dict[str, list[tuple[str, list[int]]]]
    result: list[str]


def phrase_search_trace(
    index: InvertedIndex, phrase: str, *, zone: str | None = None
) -> PhraseTrace:
    """Same semantics and result as ``scoring.phrase_search``; also reports, per zone,
    the candidates after postings intersection vs. docs passing the positional check, and
    each match's start positions (in the zone's raw token stream)."""
    terms_pos = analyze_with_positions(phrase)
    if not terms_pos:
        return PhraseTrace(offsets=[], zones=[], candidates=0, matches={}, result=[])
    first = terms_pos[0][1]
    offsets = [(term, pos - first) for term, pos in terms_pos]

    zone_traces: list[PhraseZoneTrace] = []
    all_candidates: set[str] = set()
    matches: dict[str, list[tuple[str, list[int]]]] = {}
    for z in [zone] if zone is not None else index.zones:
        plists = {term: dict(index.postings(term, z)) for term, _ in offsets}
        sizes = {term: len(p) for term, p in plists.items()}
        if any(not p for p in plists.values()):
            zone_traces.append(PhraseZoneTrace(z, sizes, 0, 0))
            continue
        ordered = sorted((sorted(p) for p in plists.values()), key=len)
        candidates = ordered[0]
        for plist in ordered[1:]:
            if not candidates:
                break
            candidates = intersect(candidates, plist)
        all_candidates.update(candidates)
        n_matched = 0
        for doc_id in candidates:
            position_sets = [(set(plists[t][doc_id]), off) for t, off in offsets]
            anchor_positions, _ = position_sets[0]
            starts = sorted(
                p for p in anchor_positions if all(p + off in ps for ps, off in position_sets)
            )
            if starts:
                n_matched += 1
                matches.setdefault(doc_id, []).append((z, starts))
        zone_traces.append(PhraseZoneTrace(z, sizes, len(candidates), n_matched))
    return PhraseTrace(
        offsets=offsets,
        zones=zone_traces,
        candidates=len(all_candidates),
        matches=matches,
        result=sorted(matches),
    )


# ---- highlighting ----------------------------------------------------------------------


def matching_surface_forms(text: str, stems: set[str] | list[str]) -> list[str]:
    """Distinct surface words in ``text`` (original case) whose analyzed stem is one of
    ``stems`` — what the UI highlights."""
    wanted = set(stems)
    if not wanted:
        return []
    seen: dict[str, None] = {}
    for tok in _TOKEN_RE.findall(unicodedata.normalize("NFKC", text)):
        folded = tok.casefold()
        if folded not in STOP_WORDS and stem(folded) in wanted:
            seen.setdefault(tok, None)
    return list(seen)


def phrase_occurrence_spans(text: str, positions: list[int], length: int) -> list[tuple[int, int]]:
    """Char spans ``(start, end)`` in ``text`` of each phrase occurrence that starts at
    raw-token ``positions`` (the positional index's positions for the zone indexed from
    ``text``) and spans ``length`` raw tokens. Empty when the token stream of ``text``
    doesn't line up with ``tokenize`` (normalization changed token boundaries)."""
    spans = [m.span() for m in _TOKEN_RE.finditer(text)]
    if len(spans) != len(tokenize(text)):
        return []
    out: list[tuple[int, int]] = []
    for p in positions:
        last = p + max(length, 1) - 1
        if 0 <= p and last < len(spans):
            out.append((spans[p][0], spans[last][1]))
    return out


def snippet_around(
    text: str,
    words: list[str],
    *,
    width: int = 320,
    anchor: tuple[int, int] | None = None,
) -> str:
    """A ~``width``-char window of ``text`` centred near ``anchor`` (a char span, e.g. a
    verified phrase occurrence) or else the first highlighted word (whole text when
    short or nothing matches near the start)."""
    if len(text) <= width:
        return text
    first = -1
    if anchor is not None:
        first = anchor[0]
    for w in words if first < 0 else []:
        m = re.search(rf"(?<![^\W_]){re.escape(w)}(?![^\W_])", text)
        if m and (first < 0 or m.start() < first):
            first = m.start()
    if first < 0:
        return text[:width].rstrip() + "…"
    start = max(0, first - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")
