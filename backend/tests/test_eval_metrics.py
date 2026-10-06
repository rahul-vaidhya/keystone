"""Hand-computed checks for the IR evaluation metrics (pure, no DB)."""

import math

import pytest

from eval.metrics import (
    average,
    evaluate_query,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

RANKED = ["d1", "d2", "d3", "d4", "d5"]
QRELS = {"d2": 1, "d4": 1, "d9": 1}  # d9 never retrieved


def test_precision_at_k():
    assert precision_at_k(RANKED, QRELS, 1) == 0.0
    assert precision_at_k(RANKED, QRELS, 2) == 0.5
    assert precision_at_k(RANKED, QRELS, 5) == pytest.approx(2 / 5)
    # k larger than the list still divides by k
    assert precision_at_k(RANKED, QRELS, 10) == pytest.approx(2 / 10)


def test_recall_at_k():
    assert recall_at_k(RANKED, QRELS, 2) == pytest.approx(1 / 3)
    assert recall_at_k(RANKED, QRELS, 5) == pytest.approx(2 / 3)
    assert recall_at_k(RANKED, {}, 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, QRELS) == 0.5
    assert reciprocal_rank(RANKED, {"d5": 1}) == pytest.approx(0.2)
    assert reciprocal_rank(RANKED, {"d5": 1}, k=4) == 0.0
    assert reciprocal_rank(RANKED, {"zz": 1}) == 0.0


def test_ndcg_binary():
    # DCG = 1/log2(3) + 1/log2(5); IDCG (3 rels, k=5) = 1 + 1/log2(3) + 1/log2(4)
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 + 1 / math.log2(3) + 0.5
    assert ndcg_at_k(RANKED, QRELS, 5) == pytest.approx(dcg / idcg)


def test_ndcg_graded_and_perfect():
    qrels = {"a": 2, "b": 1}
    assert ndcg_at_k(["a", "b", "c"], qrels, 10) == pytest.approx(1.0)
    # swapped: DCG = 1 + 2/log2(3); IDCG = 2 + 1/log2(3)
    expected = (1 + 2 / math.log2(3)) / (2 + 1 / math.log2(3))
    assert ndcg_at_k(["b", "a"], qrels, 10) == pytest.approx(expected)
    assert ndcg_at_k(["x"], qrels, 10) == 0.0
    assert ndcg_at_k(["x"], {}, 10) == 0.0


def test_evaluate_and_average():
    q1 = evaluate_query(["a"], {"a": 1})
    q2 = evaluate_query(["b"], {"a": 1})
    assert q1["P@1"] == 1.0 and q1["MRR@10"] == 1.0 and q1["nDCG@10"] == 1.0
    avg = average({"q1": q1, "q2": q2})
    assert avg["P@1"] == 0.5
    assert avg["R@100"] == 0.5
