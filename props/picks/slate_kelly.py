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
MAX_TOTAL_EXPOSURE = 1.0    # full-Kelly never leverages (sum of stakes <= bankroll); halved by KELLY_MULT
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
        wealth = np.clip(1.0 + ret @ f, 1e-6, None)    # guard ruin scenarios
        return -np.mean(np.log(wealth))
    # Full-Kelly solve with NO leverage (sum of stakes <= 1 bankroll) + per-pick cap,
    # then scale by kelly_mult. Without the total cap the optimiser over-leverages
    # when the (heuristic, understated) correlations make the slate look diversified.
    f0 = np.minimum(per_leg_half_kelly(picks), MAX_STAKE_PER_PICK)
    if f0.sum() > MAX_TOTAL_EXPOSURE:
        f0 *= MAX_TOTAL_EXPOSURE / f0.sum()
    res = minimize(neg_log_growth, f0, method="SLSQP",
                   bounds=[(0.0, MAX_STAKE_PER_PICK)] * n,
                   constraints=[{"type": "ineq", "fun": lambda f: MAX_TOTAL_EXPOSURE - f.sum()}],
                   options={"maxiter": 200, "ftol": 1e-9})
    f_full = np.clip(res.x, 0, None) if res.success else f0
    return f_full * kelly_mult


def slate_kelly_bankroll(picks, kelly_mult: float = KELLY_MULT):
    """Compound a paper bankroll staking each DAY's full slate via slate_kelly_stakes.

    `picks` is a DataFrame with columns: pick_date, player_id, game_id, team_id,
    stat_type, direction, model_prob, leg_result. Returns (curve Series indexed by
    date, metrics dict). This is the correlation-aware compounding counterpart to
    the dashboard's flat 1u curve — it sizes the heavily-clustered slates jointly
    (so 21 picks on one game aren't 21 independent bets) and compounds the result.
    """
    import pandas as pd
    d = picks[picks["leg_result"].isin(["win", "loss"])].copy()
    if d.empty:
        return pd.Series(dtype=float), {}
    bankroll, peak, max_dd = 1.0, 1.0, 0.0
    rows = []
    for day, grp in d.sort_values("pick_date").groupby("pick_date"):
        legs = [{"player_id": r.get("player_id"), "game_id": r.get("game_id"),
                 "team_id": r.get("team_id"), "stat_type": r.get("stat_type"),
                 "direction": r.get("direction"), "model_prob": float(r["model_prob"])}
                for _, r in grp.iterrows()]
        f = slate_kelly_stakes(legs, kelly_mult=kelly_mult)
        wins = (grp["leg_result"] == "win").to_numpy(dtype=float)
        day_ret = float(np.sum(f * ((NET_ODDS + 1) * wins - 1.0)))
        bankroll *= max(1e-6, 1.0 + day_ret)
        peak = max(peak, bankroll); max_dd = max(max_dd, (peak - bankroll) / peak)
        rows.append((pd.to_datetime(day), bankroll))
    curve = pd.Series(dict(rows))
    return curve, {"final_mult": bankroll, "n_days": len(rows),
                   "total_return": bankroll - 1.0, "max_dd_pct": max_dd}


def main():
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
