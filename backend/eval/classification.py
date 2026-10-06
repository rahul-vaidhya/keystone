"""Pure binary-classification metrics over (score, label) pairs — used by the citation
checker evaluation. ``label`` is ``True`` for the positive class ("supported")."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def roc_auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Area under the ROC curve via the Mann-Whitney U statistic: the probability that a
    random positive scores higher than a random negative, ties counted as 1/2. Uses
    average ranks, so it is exact with tied scores. Raises if a class is empty."""
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have the same length")
    n_pos = sum(1 for y in labels if y)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("roc_auc needs at least one positive and one negative")
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based average rank of the tie group
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rank_sum_pos = sum(r for r, y in zip(ranks, labels, strict=True) if y)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


@dataclass(frozen=True)
class Confusion:
    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def accuracy(self) -> float:
        total = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / total if total else 0.0


def confusion_at(scores: Sequence[float], labels: Sequence[bool], threshold: float) -> Confusion:
    """Predict positive iff ``score >= threshold`` (the app's ``supported`` rule)."""
    tp = fp = tn = fn = 0
    for s, y in zip(scores, labels, strict=True):
        pred = s >= threshold
        if pred and y:
            tp += 1
        elif pred:
            fp += 1
        elif y:
            fn += 1
        else:
            tn += 1
    return Confusion(tp, fp, tn, fn)


def best_f1_threshold(scores: Sequence[float], labels: Sequence[bool]) -> tuple[float, float]:
    """``(threshold, f1)`` maximizing F1 over every distinct observed score used as a
    ``>=`` cut-off. Ties on F1 resolve to the lowest threshold."""
    best_t, best_f = 0.0, -1.0
    for t in sorted(set(scores)):
        f = confusion_at(scores, labels, t).f1
        if f > best_f:
            best_t, best_f = t, f
    return best_t, max(best_f, 0.0)
