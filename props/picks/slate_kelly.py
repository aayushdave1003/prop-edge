"""Correlation-aware slate-level Kelly sizing.

The per-leg half-Kelly (`(3p-1)/4` per pick, in log_picks/predict_today) sizes each
pick as an independent 3x bet. But the recommended slate is heavily CLUSTERED —
many picks land on the same game (e.g. 21 of 25 picks on one game on 2026-06-13).
Per-leg Kelly then stakes ~21x half-Kelly on what is essentially ONE correlated
outcome → wildly over-leveraged (a single bad game sinks the whole stake).

This sizes the whole slate JOINTLY: it maximises expected log-bankroll growth over
the correlated joint outcome distribution (Gaussian copula on the picks' pairwise
correlations, same `_pairwise_rho` the parlay builder uses), then applies a
fractional-Kelly multiplier for safety. Correlated same-game clusters get
down-sized so total exposure to any one game's outcome is bounded.

`slate_kelly_stakes(picks)` -> per-pick stake fractions (of bankroll).
Demo:  DATABASE_URL=$RAILWAY_DATABASE_URL python -m props.picks.slate_kelly
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from props.picks.build_parlays import _pairwise_rho

NET_ODDS = 2.0          # a pick framed as a 3x bet (win → +2 units net), matching the per-leg Kelly
KELLY_MULT = 0.5        # half-Kelly for safety
MAX_STAKE_PER_PICK = 0.10   # cap any single pick at 10% of bankroll
N_SIMS = 20000


def _corr_matrix(picks: list[dict]) -> np.ndarray:
    n = len(picks)
    R = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            R[i, j] = R[j, i] = _pairwise_rho(picks[i], picks[j])
    w, V = np.linalg.eigh(R)                       # nearest-PSD
    R = V @ np.diag(np.clip(w, 1e-6, None)) @ V.T
    d = np.sqrt(np.diag(R)); return R / np.outer(d, d)


def _sample_wins(picks: list[dict], n_sims: int = N_SIMS, seed: int = 42) -> np.ndarray:
    """(n_sims, n) boolean win matrix from the correlated joint distribution."""
    p = np.array([pk["model_prob"] for pk in picks], dtype=float)
    L = np.linalg.cholesky(_corr_matrix(picks))
    Z = np.random.default_rng(seed).standard_normal((n_sims, len(picks))) @ L.T
    return Z < norm.ppf(p)


def per_leg_half_kelly(picks: list[dict]) -> np.ndarray:
    """The status-quo independent sizing: f_i = (3p-1)/4, clipped >=0."""
    p = np.array([pk["model_prob"] for pk in picks], dtype=float)
    return np.clip(((NET_ODDS + 1) * p - 1) / NET_ODDS * KELLY_MULT, 0, None)


def slate_kelly_stakes(picks: list[dict], n_sims: int = N_SIMS,
                       kelly_mult: float = KELLY_MULT) -> np.ndarray:
    """Correlation-aware per-pick stake fractions maximising E[log bankroll].

    Solves max_f  E[ log(1 + sum_i f_i ((b+1) w_i - 1)) ]  over the correlated
    joint win distribution, f_i in [0, MAX_STAKE_PER_PICK], then scales by
    kelly_mult. Concave in f, so a bounded gradient solve finds the optimum."""
    n = len(picks)
    if n == 0:
        return np.array([])
    wins = _sample_wins(picks, n_sims).astype(float)
    ret = (NET_ODDS + 1) * wins - 1.0                 # per-pick return per unit staked, per scenario
    def neg_log_growth(f):
        wealth = 1.0 + ret @ f
        wealth = np.clip(wealth, 1e-6, None)           # guard ruin scenarios
        return -np.mean(np.log(wealth))
    f0 = np.clip(per_leg_half_kelly(picks), 0, MAX_STAKE_PER_PICK)
    res = minimize(neg_log_growth, f0, method="L-BFGS-B",
                   bounds=[(0.0, MAX_STAKE_PER_PICK)] * n)
    return np.clip(res.x, 0, None) * (kelly_mult / KELLY_MULT if kelly_mult != KELLY_MULT else 1.0)


def main():
    from datetime import date
    from sqlalchemy import text
    from props.utils.db import engine
    from props.utils.logging import configure_logging
    from props.models.category_cutoffs import rec_cutoff
    configure_logging()
    # Pull the most clustered recent rec-tier slate to demonstrate the correction.
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date d,
                   pk.player_id, pk.game_id, pk.stat_type, pk.direction, pk.model_prob,
                   g.sport_code, g.home_team_id
            FROM picks pk JOIN games g USING (game_id)
            WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date >= CURRENT_DATE - 20
        """)).all()
    from collections import defaultdict
    by_day = defaultdict(list)
    for r in rows:
        if float(r.model_prob) >= rec_cutoff(r.sport_code, r.stat_type):
            by_day[r.d].append({"player_id": r.player_id, "game_id": r.game_id,
                                "team_id": r.home_team_id, "stat_type": r.stat_type,
                                "direction": r.direction, "model_prob": float(r.model_prob)})
    day = max(by_day, key=lambda d: len(by_day[d]))     # most picks
    picks = by_day[day]
    per_leg = per_leg_half_kelly(picks)
    slate = slate_kelly_stakes(picks)
    ngames = len({p["game_id"] for p in picks})
    print(f"\nMost-clustered rec slate: {day}  ({len(picks)} picks across {ngames} games)")
    print(f"  per-leg half-Kelly:  total stake = {per_leg.sum():.2f}x bankroll   (max single {per_leg.max():.3f})")
    print(f"  slate Kelly (corr):  total stake = {slate.sum():.2f}x bankroll   (max single {slate.max():.3f})")
    print(f"  -> slate Kelly stakes {slate.sum()/max(per_leg.sum(),1e-9):.0%} of the per-leg total "
          f"(correlation-aware down-sizing)")


if __name__ == "__main__":
    main()
