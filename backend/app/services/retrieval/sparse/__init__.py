"""From-scratch sparse IR core (pure Python, no DB, no app imports).

- ``text``    — tokenization, Unicode normalization, case folding, stop words, Porter stemming.
- ``index``   — positional, zoned inverted index (dictionary + postings, df/idf, zone
  lengths, lnc norms, champion lists).
- ``scoring`` — ranked retrieval (SMART lnc.ltc tf-idf cosine, Okapi BM25) with zone
  weighting, index elimination, champion lists and heap-based top-K; positional phrase
  queries; Boolean AND/OR/NOT; text-to-text tf-idf cosine.

Used by ``app.services.retrieval.sparse_channel`` as hybrid search's lexical channel
when ``SPARSE_RETRIEVAL_MODE != "off"``.
"""

from __future__ import annotations

from app.services.retrieval.sparse.index import InvertedIndex, SparseDoc, log_tf
from app.services.retrieval.sparse.scoring import (
    SparseHit,
    TermContribution,
    and_not,
    boolean_search,
    intersect,
    phrase_search,
    search,
    tfidf_cosine,
    union,
)
from app.services.retrieval.sparse.text import (
    STOP_WORDS,
    analyze,
    analyze_with_positions,
    stem,
    tokenize,
)

__all__ = [
    "STOP_WORDS",
    "InvertedIndex",
    "SparseDoc",
    "SparseHit",
    "TermContribution",
    "analyze",
    "analyze_with_positions",
    "and_not",
    "boolean_search",
    "intersect",
    "log_tf",
    "phrase_search",
    "search",
    "stem",
    "tfidf_cosine",
    "tokenize",
    "union",
]
