"""Offline feature-eval harness — measure a candidate feature's signal on settled
games BEFORE wiring it into a model and paying for a full retrain. This is the
weather/lineup validation pattern, generalized.

The key question isn't "does the feature correlate with the outcome" — a feature
the model already captures indirectly adds nothing even if it correlates. The
real test is whether it explains the error the CURRENT model misses. So for a
stat + a feature already present in player_games.derived, it runs the prod model
on recent settled games and reports:

  - coverage     — how often the feature is actually populated (0 = effectively missing)
  - corr(actual) — raw association with the outcome
  - corr(resid)  — THE test: association with (actual − model prediction); if the
                   model already knows it, this is ~0
  - terciles     — mean actual + mean residual by feature bucket (is it monotonic?)

Verdict flags a feature worth a full A/B retrain when it explains residual with
real coverage. Pair it with `ab_compare` (validate signal here → retrain →
A/B-gate there → `retrain_and_promote`).

Run:  python -m props.models.feature_eval --stat total_bases --feature wx_wind_out
      python -m props.models.feature_eval --stat hits --feature platoon_advantage --days 365
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from props.models.ab_compare import STAT_MODEL, _load_recent, _predict
from props.utils.logging import log, configure_logging

SIGNAL_CORR = 0.03        # |corr with residual| above this = real residual signal
MIN_COVERAGE = 0.20       # need the feature populated on >=20% of rows to trust it


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 30 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def run(stat: str, feature: str, days: int):
    configure_logging()
    if stat not in STAT_MODEL:
        raise SystemExit(f"unknown stat {stat}; choose from {list(STAT_MODEL)}")

    df = _load_recent(stat, days)
    if df.empty:
        print(f"No settled data for {stat} in the last {days}d.")
        return
    derived = pd.json_normalize(df["derived"])
    if feature not in derived.columns:
        print(f"Feature '{feature}' is not present in derived for these rows.")
        print("Available (sample):",
              ", ".join(sorted(derived.columns)[:40]), "...")
        return

    feat = pd.to_numeric(derived[feature], errors="coerce")
    y = df["y"].to_numpy(dtype=float)
    pred = _predict(Path("models") / f"{STAT_MODEL[stat]}.txt", df)
    resid = y - pred

    cov_nonnull = float(feat.notna().mean())
    cov_nonzero = float((feat.fillna(0) != 0).mean())

    mask = feat.notna().to_numpy()
    fv, yv, rv = feat.to_numpy()[mask], y[mask], resid[mask]
    corr_actual = _safe_corr(fv, yv)
    corr_resid = _safe_corr(fv, rv)

    print(f"\n=== Feature eval — {feature} → {stat} (n={len(df)}, last {days}d) ===")
    print(f"  coverage      {cov_nonzero:5.0%} non-zero  ({cov_nonnull:.0%} non-null)")
    print(f"  corr(actual)  {corr_actual:+.3f}   raw association with the outcome")
    print(f"  corr(resid)   {corr_resid:+.3f}   association with model error  ← the test")

    # bucketed: is the residual effect monotonic across the feature's range?
    try:
        nz = feat.fillna(0)
        buckets = pd.qcut(nz.rank(method="first"), 3, labels=["low", "mid", "high"])
        bt = pd.DataFrame({"f": nz, "y": y, "resid": resid, "b": buckets})
        print("  tercile        feat    actual   resid")
        for b in ["low", "mid", "high"]:
            g = bt[bt["b"] == b]
            print(f"    {b:<5}       {g['f'].mean():6.2f}  {g['y'].mean():6.2f}  "
                  f"{g['resid'].mean():+6.2f}")
    except Exception as e:
        log.info("bucket_skip", error=str(e)[:80])

    # verdict
    strong = (not np.isnan(corr_resid)) and abs(corr_resid) >= SIGNAL_CORR \
        and cov_nonzero >= MIN_COVERAGE
    if cov_nonzero < MIN_COVERAGE:
        verdict = f"⚠ sparse — only {cov_nonzero:.0%} populated; backfill before trusting"
    elif strong:
        verdict = "✅ residual signal present — worth an A/B retrain (ab_compare)"
    elif (not np.isnan(corr_actual)) and abs(corr_actual) >= SIGNAL_CORR:
        verdict = "• correlates with outcome but NOT residual — model likely already has it"
    else:
        verdict = "✗ little signal — unlikely to move MAE"
    print(f"\n  verdict: {verdict}\n")
    log.info("feature_eval", stat=stat, feature=feature, n=len(df),
             coverage=round(cov_nonzero, 3), corr_actual=round(corr_actual, 3),
             corr_resid=round(corr_resid, 3), worth_retrain=bool(strong))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stat", required=True, choices=list(STAT_MODEL))
    p.add_argument("--feature", required=True, help="a key in player_games.derived")
    p.add_argument("--days", type=int, default=180)
    args = p.parse_args()
    run(args.stat, args.feature, args.days)


if __name__ == "__main__":
    main()
