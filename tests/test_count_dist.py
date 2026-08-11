"""Unit tests for the negative-binomial count pricer (reference module) — no DB."""
import numpy as np
from scipy import stats as st

from props.models.count_dist import p_over_count, DISPERSION


def test_poisson_passthrough_for_true_counts():
    # a stat not in DISPERSION prices exactly as Poisson
    lam = np.array([0.8, 1.5, 2.2])
    got = p_over_count(np.array([1.5, 1.5, 1.5]), lam, "hits")
    exp = 1 - st.poisson.cdf(1, lam)
    assert np.allclose(got, exp)


def test_negbin_reshapes_total_bases_tails():
    # NB (over-dispersed) lowers the low tail and RAISES the high tail vs Poisson
    lam = np.full(3, 1.3)
    lines = np.array([0.5, 2.5, 3.5])
    nb = p_over_count(lines, lam, "total_bases")
    po = p_over_count(lines, lam, "hits")  # Poisson baseline
    assert nb[0] < po[0]          # over 0.5: Poisson was over-confident
    assert nb[2] > po[2]          # over 3.5: Poisson under-priced the HR tail


def test_total_bases_is_configured_overdispersed():
    assert DISPERSION["total_bases"] > 1.5   # materially over-dispersed


def test_monotonic_in_lambda_edge_preserved():
    # higher predicted mean -> higher P(over): the reshape keeps the ranking (edge)
    lines = np.array([2.5, 2.5, 2.5])
    p = p_over_count(lines, np.array([1.0, 2.0, 3.0]), "total_bases")
    assert p[0] < p[1] < p[2]
