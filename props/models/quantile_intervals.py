"""Quantile-regression interval models — honest prediction intervals.

The dashboard shows a Poisson 25-75% interval around each prediction, but the
coverage check found it runs TOO NARROW for some stats (total_bases/points cover
only ~32% of outcomes vs the 50% it claims) — the Poisson assumption
under-models the real over-dispersion. LightGBM quantile regression
(`objective=quantile`) predicts the conditional quantiles DIRECTLY, so the
interval is empirically calibrated instead of assuming a distribution.

This trains q10/q25/q75/q90 models per stat (reusing the mean model's features +
training split) and reports the empirical coverage of the quantile interval vs
the Poisson interval on the held-out test set — so we PROVE it's better before
wiring it into the prediction path / display.

Run:  python -m props.models.quantile_intervals               # total_bases + hits
      python -m props.models.quantile_intervals --stat total_bases --save
"""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from scipy.stats import poisson

from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models")
# stat -> (training module, mean-model file stem)
STATS = {
    "total_bases": ("props.models.total_bases_v1", "total_bases_v1"),
    "hits": ("props.models.hits_v1", "hits_v1"),
}
SPLIT = pd.Timestamp("2025-01-01")     # same train/test boundary as the mean models
ALPHAS = (0.10, 0.25, 0.75, 0.90)


def _coverage(actual, lo, hi) -> float:
    return float(((actual >= lo) & (actual <= hi)).mean())


def run_one(stat: str, save: bool = False) -> dict:
    mod = importlib.import_module(STATS[stat][0])
    keys = mod.FEATURE_KEYS
    df = mod.load_training_data()
    train = df[df["game_date"] < SPLIT]
    test = df[df["game_date"] >= SPLIT]
    Xtr, ytr = train[keys], train["y"]
    Xte, yte = test[keys], test["y"].to_numpy(dtype=float)

    qpred = {}
    for a in ALPHAS:
        params = {"objective": "quantile", "alpha": a, "learning_rate": 0.04,
                  "num_leaves": 31, "min_data_in_leaf": 100, "feature_fraction": 0.9,
                  "bagging_fraction": 0.9, "bagging_freq": 5, "verbose": -1, "seed": 42}
        m = lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=800)
        qpred[a] = m.predict(Xte)
        if save:
            m.save_model(str(MODEL_DIR / f"{stat}_q{int(a * 100)}.txt"))

    # quantile-interval coverage
    q_cov_50 = _coverage(yte, qpred[0.25], qpred[0.75])
    q_cov_80 = _coverage(yte, qpred[0.10], qpred[0.90])

    # Poisson-interval coverage from the current prod MEAN model (the status quo)
    booster = lgb.Booster(model_file=str(MODEL_DIR / f"{STATS[stat][1]}.txt"))
    mean = booster.predict(Xte)
    p_cov_50 = _coverage(yte, poisson.ppf(0.25, mean), poisson.ppf(0.75, mean))
    p_cov_80 = _coverage(yte, poisson.ppf(0.10, mean), poisson.ppf(0.90, mean))

    out = {"stat": stat, "n": len(yte),
           "poisson_25_75": round(p_cov_50, 3), "quantile_25_75": round(q_cov_50, 3),
           "poisson_10_90": round(p_cov_80, 3), "quantile_10_90": round(q_cov_80, 3),
           "saved": save}
    log.info("quantile_eval", **out)
    return out


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", choices=list(STATS))
    ap.add_argument("--save", action="store_true", help="save the q-models to models/")
    args = ap.parse_args()
    stats = [args.stat] if args.stat else list(STATS)
    print(f"{'stat':<12} {'n':>6}  25-75% (target .50)      10-90% (target .80)")
    print(f"{'':12} {'':>6}  poisson -> quantile       poisson -> quantile")
    for s in stats:
        r = run_one(s, save=args.save)
        print(f"{r['stat']:<12} {r['n']:>6}  {r['poisson_25_75']:.3f} -> {r['quantile_25_75']:.3f}"
              f"           {r['poisson_10_90']:.3f} -> {r['quantile_10_90']:.3f}")


if __name__ == "__main__":
    main()
