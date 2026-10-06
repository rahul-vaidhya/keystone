"""Hand-computed checks for the citation-checker eval's classification metrics (pure)."""

import pytest

from eval.classification import best_f1_threshold, confusion_at, roc_auc


def test_roc_auc_perfect_and_inverted():
    assert roc_auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert roc_auc([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == 0.0


def test_roc_auc_hand_computed():
    # positives 0.8, 0.4; negatives 0.6, 0.2 -> pairs (0.8>0.6, 0.8>0.2, 0.4<0.6, 0.4>0.2)
    assert roc_auc([0.8, 0.4, 0.6, 0.2], [True, True, False, False]) == pytest.approx(3 / 4)


def test_roc_auc_ties_count_half():
    assert roc_auc([0.5, 0.5], [True, False]) == pytest.approx(0.5)
    # pos 0.5 vs negs 0.5 (tie, 1/2) and 0.1 (win) -> 1.5 / 2
    assert roc_auc([0.5, 0.5, 0.1], [True, False, False]) == pytest.approx(0.75)


def test_roc_auc_needs_both_classes():
    with pytest.raises(ValueError):
        roc_auc([0.1, 0.2], [True, True])


def test_confusion_at_threshold_is_inclusive():
    c = confusion_at([0.9, 0.33, 0.2, 0.5], [True, True, True, False], 0.33)
    assert (c.tp, c.fp, c.tn, c.fn) == (2, 1, 0, 1)
    assert c.precision == pytest.approx(2 / 3)
    assert c.recall == pytest.approx(2 / 3)
    assert c.f1 == pytest.approx(2 / 3)
    assert c.accuracy == pytest.approx(2 / 4)


def test_confusion_empty_denominators_are_zero():
    c = confusion_at([0.1], [False], 0.5)
    assert c.precision == 0.0 and c.recall == 0.0 and c.f1 == 0.0 and c.accuracy == 1.0


def test_best_f1_threshold():
    t, f = best_f1_threshold([0.9, 0.7, 0.6, 0.3], [True, True, False, False])
    assert t == 0.7 and f == 1.0
