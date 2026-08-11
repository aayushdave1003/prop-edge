"""Count-distribution pricing: P(stat > line | predicted mean λ).

Poisson (var = mean) is correct for TRUE counts — hits and pitcher strikeouts have a
measured within-player dispersion ≈ 1.0 — but WRONG for compound stats like
total_bases, where a home run is 4 bases, so the variance ≈ 2.4× the mean. Pricing TB
as Poisson over-prices the low tail (P(over 0.5) predicted 0.81 vs 0.64 actual) and
UNDER-prices the HR-driven high tail (P(over 3.5) predicted 0.09 vs 0.15 actual);
see `resolution_audit --dist` and the held-out A/B (pooled Brier 0.2018 → 0.1907).

For stats in DISPERSION we price with a negative binomial matched to the mean λ and
the empirical dispersion φ = var/mean ≈ 2.2:

    nbinom(n = λ/(φ−1), p = 1/φ)   →   mean = λ,  var = φ·λ

Poisson is the φ→1 limit. This is purely a CALIBRATION fix: it reshapes P(over) at a
fixed λ, so the ranking by λ (hence AUC / resolution / edge) is UNCHANGED.

STATUS — REFERENCE ONLY, NOT WIRED INTO PRODUCTION (2026-08). The held-out A/B showed
NB fixes the RAW calibration (pooled Brier 0.2018 → 0.1910) — but the production path
runs an isotonic calibrator on top, and the honest ship gate (fit BOTH calibrators on
the earlier 60%, score the later 40%) found the isotonic step ALREADY ABSORBS the
Poisson mis-spec: NB+isotonic 0.1921 vs Poisson+isotonic 0.1923 — a tie (Δ 0.1%, well
inside the ~0.003 SE). So per "ship the truth, don't ship a change that doesn't
demonstrably help," the base distribution was left as Poisson. This module stays as
the dispersion diagnostic (`--fit`) and a reference NB pricer — a genuine finding
(TB is over-dispersed) whose practical effect the existing calibration already covers.

φ is the model-RESIDUAL dispersion (variance of the actual around the model's λ, not
the raw stat), estimated leak-free on forward predictions. Re-estimate: `--fit`.
"""
import argparse

import numpy as np
from scipy import stats as st

# model-residual dispersion φ = Σ(actual−λ)² / Σλ on leak-free forward predictions
# (2026-08, n=2206): total_bases=2.21. 1.0 = Poisson; only materially over-dispersed
# stats are listed (hits 0.93 and strikeouts 1.18 stay Poisson), everything else
# stays Poisson. Re-estimate with `python -m props.models.count_dist --fit`.
DISPERSION: dict[str, float] = {"total_bases": 2.21}


def p_over_count(line, lam, stat: str):
    """P(stat > line) for scalar or array λ. Negative binomial for over-dispersed
    stats (φ>1 in DISPERSION), Poisson otherwise. `line` may be fractional (2.5) —
    P(X > 2.5) = P(X ≥ 3) = 1 − cdf(2)."""
    lam = np.asarray(lam, dtype=float)
    k = np.floor(np.asarray(line, dtype=float)).astype(int)
    phi = DISPERSION.get(stat, 1.0) if isinstance(stat, str) else 1.0
    if phi <= 1.0:
        return 1.0 - st.poisson.cdf(k, lam)
    n = np.maximum(lam, 1e-9) / (phi - 1.0)          # nbinom size parameter
    return 1.0 - st.nbinom.cdf(k, n, 1.0 / phi)


def estimate_dispersion(stat: str) -> dict:
    """Leak-free model-residual dispersion for a stat: φ = Σ(actual−λ)²/Σλ over forward
    predictions (predicted_at < game start). Also reports the Poisson baseline."""
    from sqlalchemy import text
    from props.utils.db import engine
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT pr.predicted_mean::float AS lam, (pg.stats->>:s)::float AS actual
            FROM predictions pr
            JOIN games g ON g.game_id = pr.game_id
            JOIN player_games pg ON pg.player_id = pr.player_id AND pg.game_id = pr.game_id
            WHERE pr.stat_type = :s AND pr.predicted_mean > 0 AND pr.predicted_at < g.game_datetime
              AND pg.stats ? :s AND COALESCE(pg.did_play, true) AND (pg.stats->>:s) ~ '^[0-9.]+$'
        """), {"s": stat}).all()
    if not rows:
        return {"stat": stat, "n": 0, "phi": None}
    num = sum((a - l) ** 2 for l, a in rows)
    den = sum(l for l, _ in rows)
    return {"stat": stat, "n": len(rows), "phi": (num / den if den else None)}


def main():
    p = argparse.ArgumentParser(description="Estimate model-residual dispersion φ per stat")
    p.add_argument("--fit", nargs="*", default=None,
                   help="stats to estimate φ for (default: total_bases, hits, strikeouts_pitcher)")
    args = p.parse_args()
    args.fit = args.fit or ["total_bases", "hits", "strikeouts_pitcher"]
    print("model-residual dispersion φ = Σ(actual−λ)²/Σλ  (Poisson=1.0):")
    for stat in args.fit:
        r = estimate_dispersion(stat)
        cur = DISPERSION.get(stat, 1.0)
        phi = f"{r['phi']:.2f}" if r["phi"] else "no data"
        print(f"  {stat:22} n={r['n']:>5}  φ={phi}   (priced with φ={cur})")


if __name__ == "__main__":
    main()
