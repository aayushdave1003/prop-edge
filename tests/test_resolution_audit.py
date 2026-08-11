"""Unit tests for the resolution audit's discrimination math — pure, no DB."""
from props.models.resolution_audit import auc, brier_decomp


def test_auc_perfect_separation():
    rows = [{"p": 0.9, "y": 1}, {"p": 0.8, "y": 1}, {"p": 0.2, "y": 0}, {"p": 0.1, "y": 0}]
    a, se, npos, nneg = auc(rows)
    assert abs(a - 1.0) < 1e-9 and (npos, nneg) == (2, 2)


def test_auc_reversed_is_zero():
    rows = [{"p": 0.1, "y": 1}, {"p": 0.2, "y": 1}, {"p": 0.8, "y": 0}, {"p": 0.9, "y": 0}]
    assert abs(auc(rows)[0] - 0.0) < 1e-9


def test_auc_ties_are_half():
    # all identical predictions -> pure coin flip regardless of outcome
    rows = [{"p": 0.5, "y": 1}, {"p": 0.5, "y": 0}, {"p": 0.5, "y": 1}, {"p": 0.5, "y": 0}]
    assert abs(auc(rows)[0] - 0.5) < 1e-9


def test_auc_degenerate_single_class():
    # no negatives -> AUC undefined (None), not a crash
    assert auc([{"p": 0.7, "y": 1}, {"p": 0.6, "y": 1}])[0] is None


def test_brier_resolution_zero_when_no_discrimination():
    # predictions uncorrelated with outcome -> resolution ~ 0
    rows = [{"p": 0.6, "y": 1}, {"p": 0.6, "y": 0}, {"p": 0.6, "y": 1}, {"p": 0.6, "y": 0}]
    bd = brier_decomp(rows)
    assert bd["res"] < 1e-9  # single populated bin -> ob == obar -> resolution 0
