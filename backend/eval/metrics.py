"""Pure IR metrics over a ranked list of doc ids and a qrels dict {doc_id: grade}.

Only docs with grade > 0 count as relevant. nDCG uses linear gain (gain = grade) with a
log2(rank + 1) discount and the standard ideal DCG over the sorted qrels grades — the
trec_eval / BEIR convention.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _relevant(qrels: Mapping[str, int]) -> set[str]:
    return {d for d, g in qrels.items() if g > 0}


def precision_at_k(ranked: Sequence[str], qrels: Mapping[str, int], k: int) -> float:
    rel = _relevant(qrels)
    return sum(1 for d in ranked[:k] if d in rel) / k


def recall_at_k(ranked: Sequence[str], qrels: Mapping[str, int], k: int) -> float:
    rel = _relevant(qrels)
    if not rel:
        return 0.0
    return sum(1 for d in ranked[:k] if d in rel) / len(rel)


def reciprocal_rank(ranked: Sequence[str], qrels: Mapping[str, int], k: int | None = None) -> float:
    rel = _relevant(qrels)
    for i, d in enumerate(ranked[:k] if k else ranked, start=1):
        if d in rel:
            return 1.0 / i
    return 0.0


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]))


def ndcg_at_k(ranked: Sequence[str], qrels: Mapping[str, int], k: int) -> float:
    gains = [max(qrels.get(d, 0), 0) for d in ranked[:k]]
    ideal = sorted((g for g in qrels.values() if g > 0), reverse=True)
    idcg = dcg_at_k(ideal, k)
    return dcg_at_k(gains, k) / idcg if idcg > 0 else 0.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_query(ranked: Sequence[str], qrels: Mapping[str, int]) -> dict[str, float]:
    return {
        "P@1": precision_at_k(ranked, qrels, 1),
        "P@5": precision_at_k(ranked, qrels, 5),
        "P@10": precision_at_k(ranked, qrels, 10),
        "R@10": recall_at_k(ranked, qrels, 10),
        "R@100": recall_at_k(ranked, qrels, 100),
        "MRR@10": reciprocal_rank(ranked, qrels, 10),
        "nDCG@10": ndcg_at_k(ranked, qrels, 10),
    }


def average(per_query: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    if not per_query:
        return {}
    keys = next(iter(per_query.values())).keys()
    return {m: mean([q[m] for q in per_query.values()]) for m in keys}
