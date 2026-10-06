"""Positional, zoned inverted index (IIR ch. 1-2, §6.1, §7.1).

Structures built once by ``InvertedIndex.build``:

- **Dictionary** (IIR §1.2): ``term -> df`` — document frequency, counting a document
  once even if the term occurs in several of its zones.
- **Positional postings** (IIR §2.4.2), one list per ``(zone, term)`` — a **zone index**
  (IIR §6.1: the zone is encoded in the dictionary key, so ``heading:bond`` and
  ``body:bond`` are separate postings lists). Each posting is ``(doc_id, positions)``;
  lists are sorted by ``doc_id`` so they can be merged/intersected linearly.
- **Document-level postings** per term (union across zones, sorted doc ids) — used by
  Boolean retrieval's merge-intersection.
- **Zone lengths**: per-doc, per-zone token count (analyzed terms), plus the corpus
  average per zone — BM25's length normalization (IIR §11.4.3).
- **lnc document-vector norms**: per-doc, per-zone Euclidean length of the log-tf
  vector, ``sqrt(sum_t (1 + log10 tf_t,d)^2)`` — precomputed so SMART lnc.ltc cosine
  scoring (IIR §6.4.3) only needs a division at query time.
- **Champion lists** (IIR §7.1.3): per ``(zone, term)``, the ``r`` postings with the
  highest tf (ties broken by doc id), kept in doc-id order.

Term-accepting methods (``df``/``idf``/``postings``/...) take dictionary terms (stems,
the output of ``text.analyze``). For convenience a raw word that is not itself a
dictionary term is passed through ``analyze`` first, so ``idf("bonds") == idf("bond")``.

Pure module: no DB, no app imports.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.services.retrieval.sparse.text import analyze, analyze_with_positions

Posting = tuple[str, list[int]]


@dataclass(frozen=True)
class SparseDoc:
    """One indexable unit. ``zones`` maps zone name -> text, e.g.
    ``{"heading": "...", "body": "..."}`` (IIR §6.1 zone indexes)."""

    doc_id: str
    zones: dict[str, str]


def log_tf(tf: int) -> float:
    """Sublinear tf scaling (IIR §6.4.1): ``1 + log10(tf)`` for tf > 0, else 0."""
    return 1.0 + math.log10(tf) if tf > 0 else 0.0


class InvertedIndex:
    """Immutable after ``build``. See the module docstring for the stored structures."""

    def __init__(self) -> None:
        self.n_docs: int = 0
        self.doc_ids: list[str] = []
        self.zones: list[str] = []
        self.champion_r: int = 0
        self._df: dict[str, int] = {}
        self._postings: dict[tuple[str, str], list[Posting]] = {}
        self._tf: dict[tuple[str, str], dict[str, int]] = {}
        self._doc_postings: dict[str, list[str]] = {}
        self._zone_len: dict[str, dict[str, int]] = {}
        self._avg_zone_len: dict[str, float] = {}
        self._norms: dict[str, dict[str, float]] = {}
        self._champions: dict[tuple[str, str], list[Posting]] = {}

    @classmethod
    def build(cls, docs: Iterable[SparseDoc], *, champion_r: int = 50) -> InvertedIndex:
        """Index construction: collect ``(zone, term, doc, position)`` occurrences, then
        sort each postings list by doc id (IIR §1.2). A duplicate ``doc_id`` keeps only
        its last occurrence."""
        index = cls()
        index.champion_r = champion_r
        by_id: dict[str, SparseDoc] = {d.doc_id: d for d in docs}
        index.doc_ids = sorted(by_id)
        index.n_docs = len(index.doc_ids)

        raw: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(dict)
        doc_terms: dict[str, set[str]] = defaultdict(set)
        zones: set[str] = set()
        zone_len: dict[str, dict[str, int]] = defaultdict(dict)
        for doc_id in index.doc_ids:
            for zone, text in by_id[doc_id].zones.items():
                zones.add(zone)
                terms = analyze_with_positions(text or "")
                zone_len[zone][doc_id] = len(terms)
                for term, pos in terms:
                    raw[(zone, term)].setdefault(doc_id, []).append(pos)
                    doc_terms[term].add(doc_id)

        index.zones = sorted(zones)
        index._zone_len = {z: dict(zone_len[z]) for z in index.zones}
        index._avg_zone_len = {
            z: (sum(lens.values()) / index.n_docs if index.n_docs else 0.0)
            for z, lens in index._zone_len.items()
        }
        index._df = {term: len(ids) for term, ids in doc_terms.items()}
        index._doc_postings = {term: sorted(ids) for term, ids in doc_terms.items()}

        sq_norms: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for (zone, term), per_doc in raw.items():
            plist = sorted((d, sorted(p)) for d, p in per_doc.items())
            index._postings[(zone, term)] = plist
            index._tf[(zone, term)] = {d: len(p) for d, p in plist}
            for d, p in plist:
                sq_norms[zone][d] += log_tf(len(p)) ** 2
            champs = sorted(plist, key=lambda dp: (-len(dp[1]), dp[0]))[:champion_r]
            index._champions[(zone, term)] = sorted(champs)
        index._norms = {z: {d: math.sqrt(v) for d, v in m.items()} for z, m in sq_norms.items()}
        return index

    # ---- term resolution ---------------------------------------------------------------
    def _resolve(self, term: str) -> str:
        if term in self._df:
            return term
        analyzed = analyze(term)
        return analyzed[0] if len(analyzed) == 1 else term

    # ---- dictionary --------------------------------------------------------------------
    def df(self, term: str) -> int:
        """Document frequency: number of documents containing ``term`` in ANY zone."""
        return self._df.get(self._resolve(term), 0)

    def idf(self, term: str) -> float:
        """Inverse document frequency (IIR §6.2.1): ``log10(N / df)``; 0 when df == 0."""
        df = self.df(term)
        return math.log10(self.n_docs / df) if df else 0.0

    def vocabulary_size(self) -> int:
        """Number of distinct dictionary terms (across all zones)."""
        return len(self._df)

    def terms(self) -> list[str]:
        """The dictionary, sorted."""
        return sorted(self._df)

    # ---- postings ----------------------------------------------------------------------
    def postings(self, term: str, zone: str) -> list[tuple[str, list[int]]]:
        """Positional postings list for ``term`` in ``zone``: ``[(doc_id, positions)]``
        sorted by doc id. Positions are offsets in the zone's raw token stream (stop
        words included), so phrase queries measure true word distance."""
        return self._postings.get((zone, self._resolve(term)), [])

    def doc_postings(self, term: str) -> list[str]:
        """Document-level postings (sorted doc ids, any zone) — Boolean retrieval input."""
        return self._doc_postings.get(self._resolve(term), [])

    def champions(self, term: str, zone: str) -> list[tuple[str, list[int]]]:
        """Champion list (IIR §7.1.3): the top-``champion_r`` postings by tf."""
        return self._champions.get((zone, self._resolve(term)), [])

    def tf(self, term: str, zone: str, doc_id: str) -> int:
        """Raw term frequency of ``term`` in ``doc_id``'s ``zone``."""
        return self._tf.get((zone, self._resolve(term)), {}).get(doc_id, 0)

    def tf_map(self, term: str, zone: str) -> dict[str, int]:
        """``doc_id -> tf`` for one ``(zone, term)`` postings list."""
        return self._tf.get((zone, self._resolve(term)), {})

    # ---- length statistics -------------------------------------------------------------
    def zone_length(self, doc_id: str, zone: str) -> int:
        """Number of index terms in ``doc_id``'s ``zone`` (BM25's L_d)."""
        return self._zone_len.get(zone, {}).get(doc_id, 0)

    def avg_zone_length(self, zone: str) -> float:
        """Corpus-average zone length (BM25's L_ave)."""
        return self._avg_zone_len.get(zone, 0.0)

    def doc_norm(self, doc_id: str, zone: str) -> float:
        """Precomputed lnc vector length of ``doc_id``'s ``zone``."""
        return self._norms.get(zone, {}).get(doc_id, 0.0)
