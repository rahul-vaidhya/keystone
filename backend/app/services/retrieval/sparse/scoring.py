"""Query processing over an ``InvertedIndex``: ranked retrieval (SMART lnc.ltc tf-idf
cosine and Okapi BM25), positional phrase queries, Boolean queries, and a tf-idf cosine
between two arbitrary texts.

IR concepts implemented here (IIR = Manning, Raghavan & Schütze, *Introduction to
Information Retrieval*):

- **Term-at-a-time scoring** (IIR §6.3.3, Fig. 6.14 ``CosineScore``): walk each query
  term's postings list, adding that term's contribution into a per-document accumulator.
- **SMART lnc.ltc** (IIR §6.4.3): document = **l**og-tf, **n**o idf, **c**osine
  normalized; query = **l**og-tf, **t** idf, **c**osine normalized. Score = dot product
  = cosine similarity of the two normalized vectors.
- **Okapi BM25** (IIR §11.4.3, eq. 11.32): ``idf * (k1 + 1) tf / (k1((1 - b) + b *
  L_d / L_ave) + tf)`` with ``idf = log10(N / df)`` (the IIR form, so idf matches the
  tf-idf scheme and the index's ``idf``).
- **Zone weighting** (IIR §6.1.1 weighted zone scoring): each zone is scored
  independently and the final score is ``sum_z w_z * score_z``.
- **Index elimination** (IIR §7.1.2): query terms with ``idf <= idf_threshold`` are
  dropped before scoring (low-idf terms are near-stop-words; skipping their long
  postings lists saves work and barely changes the ranking).
- **Champion lists** (IIR §7.1.3): optionally restrict the candidate set to the union of
  the query terms' champion lists, then score only those candidates.
- **Heap-based top-K selection** (IIR §7.1): ``heapq.nlargest`` over the accumulators
  (O(N log K)) instead of a full sort; ties broken deterministically by ascending doc id.
- **Positional phrase queries** (IIR §2.4.2): intersect postings, then check positions.
- **Boolean retrieval** (IIR §1.3): merge-based postings intersection processed in
  increasing-df order (IIR §1.3 "query optimization"), union for OR, AND NOT.

Pure module: no DB, no app imports.
"""

from __future__ import annotations

import heapq
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from operator import itemgetter
from typing import Literal

from app.services.retrieval.sparse.index import InvertedIndex, log_tf
from app.services.retrieval.sparse.text import analyze, analyze_with_positions


@dataclass(frozen=True)
class TermContribution:
    """One (term, zone) pair's share of a hit's final score — the "explain" output.
    ``weight`` already includes the zone weight, so a hit's contributions sum to its
    ``score``."""

    term: str
    zone: str
    tf: int
    idf: float
    weight: float


@dataclass(frozen=True)
class SparseHit:
    doc_id: str
    score: float
    contributions: tuple[TermContribution, ...] = ()


# ---- ranked retrieval ------------------------------------------------------------------


def _query_terms(index: InvertedIndex, query: str, idf_threshold: float) -> Counter[str]:
    """Analyze the query and apply index elimination: keep only terms whose idf is
    strictly greater than ``idf_threshold`` (with the default 0.0 this drops terms absent
    from the corpus and terms occurring in every document)."""
    counts = Counter(analyze(query))
    return Counter({t: c for t, c in counts.items() if index.idf(t) > idf_threshold})


def _active_zones(index: InvertedIndex, zone_weights: dict[str, float] | None) -> dict[str, float]:
    if zone_weights is None:
        return {z: 1.0 for z in index.zones}
    return {z: w for z, w in zone_weights.items() if w and z in index.zones}


def _champion_candidates(
    index: InvertedIndex, terms: Counter[str], zones: dict[str, float]
) -> set[str]:
    """Union of the champion lists of every (query term, active zone)."""
    candidates: set[str] = set()
    for term in terms:
        for zone in zones:
            candidates.update(doc_id for doc_id, _ in index.champions(term, zone))
    return candidates


def search(
    index: InvertedIndex,
    query: str,
    *,
    k: int = 10,
    scheme: Literal["tfidf", "bm25"] = "bm25",
    zone_weights: dict[str, float] | None = None,
    use_champions: bool = False,
    idf_threshold: float = 0.0,
    bm25_k1: float = 1.2,
    bm25_b: float = 0.75,
    explain: bool = False,
) -> list[SparseHit]:
    """Ranked retrieval: term-at-a-time accumulation over postings, then heap-based
    top-K. ``scheme="tfidf"`` is SMART lnc.ltc cosine per zone; ``scheme="bm25"`` is Okapi
    BM25 per zone. Final score = ``sum_z w_z * score_z`` (``zone_weights=None`` => every
    indexed zone with weight 1.0). Only documents with a positive score are returned."""
    if scheme not in ("tfidf", "bm25"):
        raise ValueError(f"unknown scheme {scheme!r}")
    terms = _query_terms(index, query, idf_threshold)
    zones = _active_zones(index, zone_weights)
    if not terms or not zones or k <= 0:
        return []

    idf = {t: index.idf(t) for t in terms}
    # ltc query weights (only used by tf-idf): log-tf * idf, cosine-normalized.
    q_weights = {t: log_tf(c) * idf[t] for t, c in terms.items()}
    q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
    if scheme == "tfidf" and q_norm == 0.0:
        return []

    candidates = _champion_candidates(index, terms, zones) if use_champions else None

    scores: dict[str, float] = defaultdict(float)
    contribs: dict[str, list[TermContribution]] = defaultdict(list)
    for zone, z_weight in zones.items():
        avg_len = index.avg_zone_length(zone)
        for term in terms:
            for doc_id, positions in index.postings(term, zone):
                if candidates is not None and doc_id not in candidates:
                    continue
                tf = len(positions)
                if scheme == "tfidf":
                    norm = index.doc_norm(doc_id, zone)
                    if norm == 0.0:
                        continue
                    term_score = (q_weights[term] / q_norm) * (log_tf(tf) / norm)
                else:
                    if avg_len == 0.0:
                        continue
                    length_ratio = index.zone_length(doc_id, zone) / avg_len
                    denom = bm25_k1 * ((1.0 - bm25_b) + bm25_b * length_ratio) + tf
                    term_score = idf[term] * ((bm25_k1 + 1.0) * tf) / denom
                weight = z_weight * term_score
                scores[doc_id] += weight
                if explain:
                    contribs[doc_id].append(
                        TermContribution(term=term, zone=zone, tf=tf, idf=idf[term], weight=weight)
                    )

    # Heap-based top-K. Pre-sorting by doc id + nlargest's stability (it behaves like
    # sorted(..., reverse=True)[:k]) gives a deterministic ascending-doc-id tie-break.
    positive = sorted((d, s) for d, s in scores.items() if s > 0.0)
    top = heapq.nlargest(k, positive, key=itemgetter(1))
    return [
        SparseHit(
            doc_id=doc_id,
            score=score,
            contributions=tuple(sorted(contribs[doc_id], key=lambda c: -c.weight))
            if explain
            else (),
        )
        for doc_id, score in top
    ]


# ---- postings-list merge algorithms (IIR §1.3) -----------------------------------------


def intersect(p1: list[str], p2: list[str]) -> list[str]:
    """Linear-time merge intersection of two sorted postings lists (IIR Fig. 1.6)."""
    answer: list[str] = []
    i = j = 0
    while i < len(p1) and j < len(p2):
        if p1[i] == p2[j]:
            answer.append(p1[i])
            i += 1
            j += 1
        elif p1[i] < p2[j]:
            i += 1
        else:
            j += 1
    return answer


def union(p1: list[str], p2: list[str]) -> list[str]:
    """Linear-time merge union of two sorted postings lists (Boolean OR)."""
    answer: list[str] = []
    i = j = 0
    while i < len(p1) or j < len(p2):
        if j >= len(p2) or (i < len(p1) and p1[i] < p2[j]):
            answer.append(p1[i])
            i += 1
        elif i >= len(p1) or p2[j] < p1[i]:
            answer.append(p2[j])
            j += 1
        else:
            answer.append(p1[i])
            i += 1
            j += 1
    return answer


def and_not(p1: list[str], p2: list[str]) -> list[str]:
    """Linear-time merge of ``p1 AND NOT p2`` over sorted postings lists."""
    answer: list[str] = []
    i = j = 0
    while i < len(p1):
        if j >= len(p2) or p1[i] < p2[j]:
            answer.append(p1[i])
            i += 1
        elif p1[i] == p2[j]:
            i += 1
            j += 1
        else:
            j += 1
    return answer


def _intersect_many(lists: list[list[str]]) -> list[str]:
    """Conjunctive query optimization (IIR §1.3): process postings in increasing
    length (= df) order so intermediate results stay as small as possible."""
    ordered = sorted(lists, key=len)
    result = ordered[0]
    for plist in ordered[1:]:
        if not result:
            break
        result = intersect(result, plist)
    return result


# ---- Boolean retrieval -----------------------------------------------------------------


def boolean_search(index: InvertedIndex, query: str) -> list[str]:
    """Boolean retrieval (IIR §1.3). Operators are uppercase ``AND``/``OR``/``NOT``;
    adjacent operands are implicitly ANDed; ``AND`` binds tighter than ``OR`` (the query
    is a disjunction of conjunctive clauses). Each operand word goes through the same
    analysis pipeline as the index (stop words are ignored; a word that analyzes to
    several terms is ANDed). Each clause intersects its positive postings in
    increasing-df order, then applies ``AND NOT`` for negated operands (a clause with
    only negations starts from all documents). Returns sorted doc ids."""
    clauses: list[list[tuple[bool, list[str]]]] = [[]]
    negate = False
    for raw in query.split():
        if raw == "OR":
            clauses.append([])
            negate = False
        elif raw == "AND":
            continue
        elif raw == "NOT":
            negate = not negate
        else:
            terms = analyze(raw)
            if terms:
                clauses[-1].append((negate, terms))
            negate = False

    result: list[str] = []
    for clause in clauses:
        if not clause:
            continue
        positives = [index.doc_postings(t) for neg, ts in clause if not neg for t in ts]
        matched = _intersect_many(positives) if positives else list(index.doc_ids)
        for neg, ts in clause:
            if neg:
                # NOT (a b) for a multi-term word = NOT (a AND b).
                matched = and_not(matched, _intersect_many([index.doc_postings(t) for t in ts]))
        result = union(result, matched)
    return result


# ---- positional phrase queries ---------------------------------------------------------


def phrase_search(index: InvertedIndex, phrase: str, *, zone: str | None = None) -> list[str]:
    """Positional-index phrase query (IIR §2.4.2). The phrase is analyzed like any text;
    each remaining term keeps its offset in the phrase's raw token stream, so removed stop
    words still count as gaps (``"rules of thumb"`` requires ``thumb`` exactly two
    positions after ``rule``; any word may fill the gap). Candidates are the intersection
    of the terms' postings (increasing-df order), then positions are verified. Searches
    one ``zone`` or, when ``None``, every zone (a match in any zone counts). Returns
    sorted doc ids."""
    terms_pos = analyze_with_positions(phrase)
    if not terms_pos:
        return []
    first = terms_pos[0][1]
    offsets = [(term, pos - first) for term, pos in terms_pos]

    matched: set[str] = set()
    for z in [zone] if zone is not None else index.zones:
        plists = {term: dict(index.postings(term, z)) for term, _ in offsets}
        if any(not p for p in plists.values()):
            continue
        candidates = _intersect_many([sorted(p) for p in plists.values()])
        for doc_id in candidates:
            position_sets = [(set(plists[t][doc_id]), off) for t, off in offsets]
            anchor_positions, _ = position_sets[0]
            if any(all(p + off in ps for ps, off in position_sets) for p in anchor_positions):
                matched.add(doc_id)
    return sorted(matched)


# ---- text-to-text similarity -----------------------------------------------------------


def _ltc_vector(index: InvertedIndex, text: str) -> dict[str, float]:
    counts = Counter(analyze(text))
    vec = {t: log_tf(c) * index.idf(t) for t, c in counts.items()}
    return {t: w for t, w in vec.items() if w > 0.0}


def tfidf_cosine(index: InvertedIndex, text_a: str, text_b: str) -> float:
    """Cosine similarity of two arbitrary texts as ltc vectors (log-tf * idf, using the
    index's corpus idf, cosine-normalized) — IIR §6.3. Returns 0.0 if either vector is
    empty (e.g. only stop words / out-of-vocabulary terms)."""
    a = _ltc_vector(index, text_a)
    b = _ltc_vector(index, text_b)
    norm_a = math.sqrt(sum(w * w for w in a.values()))
    norm_b = math.sqrt(sum(w * w for w in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(w * b[t] for t, w in a.items() if t in b)
    return dot / (norm_a * norm_b)
