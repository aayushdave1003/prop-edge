"""Regression tests that lock in the track-record integrity work.

The whole point of ``honest_oos`` is that it CANNOT be fooled the way the old
in-sample measurement was. These tests pin that guarantee so a future refactor
can't quietly re-introduce the leak, and pin the paper-Kelly gate that only sizes
categories with a demonstrated edge.
"""
import time

import pytest

from props.models.category_cutoffs import BREAKEVEN
from props.models import honest_oos as ho


# ── Wilson CI ─────────────────────────────────────────────────────────────────
def test_wilson_ci_empty_is_full_interval():
    assert ho.wilson_ci(0, 0) == (0.0, 1.0)


def test_wilson_ci_brackets_point_estimate():
    lo, hi = ho.wilson_ci(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0


def test_wilson_ci_narrows_with_n():
    w_small = (lambda lo, hi: hi - lo)(*ho.wilson_ci(5, 10))
    w_large = (lambda lo, hi: hi - lo)(*ho.wilson_ci(500, 1000))
    assert w_large < w_small


# ── the anti-leak guarantee (the core of the whole effort) ────────────────────
def _honest_rate(picks):
    rec = ho.walk_forward_oos(picks)
    return ho._summ(rec)["hit"], len(rec)


def test_pure_noise_is_measured_at_coinflip():
    """model_prob ⟂ outcome (true 50%). The point-in-time harness must report
    ~50% — it must NOT manufacture an edge from noise."""
    noise = ho._synth("noise", seed=1)
    hit, n = _honest_rate(noise)
    assert n > 0
    assert abs(hit - 0.5) < 0.06, hit


def test_in_sample_method_inflates_but_honest_does_not():
    """On the SAME noise, the old in-sample selection inflates above breakeven
    while the honest harness stays ~50%. This is the exact bug + fix, pinned."""
    noise = ho._synth("leaky", seed=2)
    honest_hit, _ = _honest_rate(noise)
    leaky = ho.leaky_insample_recommended(noise)
    leaky_hit = ho._summ(leaky)["hit"]
    assert leaky_hit > BREAKEVEN, "broken in-sample method should over-report"
    assert abs(honest_hit - 0.5) < 0.06, "honest harness must stay at coin-flip"


def test_real_signal_clears_breakeven():
    """A genuinely calibrated edge must survive the harness (no false negatives)."""
    sig = ho._synth("signal", seed=3)
    hit, _ = _honest_rate(sig)
    assert hit > BREAKEVEN, hit


def test_brier_is_zero_for_perfect_and_one_for_worst():
    perfect = [{"model_prob": 1.0, "win": 1}, {"model_prob": 0.0, "win": 0}]
    worst = [{"model_prob": 0.0, "win": 1}, {"model_prob": 1.0, "win": 0}]
    assert ho.brier(perfect) == pytest.approx(0.0)
    assert ho.brier(worst) == pytest.approx(1.0)


# ── honest verdict labels ─────────────────────────────────────────────────────
@pytest.mark.parametrize("lo,hi,expected_contains", [
    (BREAKEVEN + 0.01, 0.9, "EDGE"),          # CI floor clears breakeven
    (0.30, BREAKEVEN - 0.01, "below"),        # CI ceiling misses breakeven
    (0.40, 0.70, "not proven"),               # straddles breakeven
])
def test_verdict_labels(lo, hi, expected_contains):
    s = {"n": 50, "lo": lo, "hi": hi}
    assert expected_contains in ho._verdict(s)


def test_verdict_empty():
    assert ho._verdict({"n": 0, "lo": 0.0, "hi": 1.0}) == "—"


# ── paper-Kelly gate: only proven-edge categories get a stake ─────────────────
def test_kelly_gate_only_sizes_proven_categories():
    repo = pytest.importorskip("props.api.repo")
    # Seed the cache so no DB call happens (fresh timestamp = within TTL).
    repo._PROVEN_CACHE.update(t=time.time(), keys={"sport:mlb", "cat:wnba|points|under"})
    try:
        assert repo._is_proven("mlb", "hits", "over") is True       # proven by sport
        assert repo._is_proven("wnba", "points", "under") is True   # proven by category
        assert repo._is_proven("wnba", "points", "over") is False   # not proven
        assert repo._is_proven("nba", "assists", "over") is False
    finally:
        repo._PROVEN_CACHE.update(t=0.0, keys=None)   # reset so real code recomputes


def test_kelly_gate_empty_set_sizes_nothing():
    repo = pytest.importorskip("props.api.repo")
    repo._PROVEN_CACHE.update(t=time.time(), keys=set())
    try:
        assert repo._is_proven("mlb", "hits", "under") is False
    finally:
        repo._PROVEN_CACHE.update(t=0.0, keys=None)
