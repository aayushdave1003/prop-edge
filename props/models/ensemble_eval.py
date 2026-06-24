"""Ensemble-eval harness — does stacking/blending beat the single Poisson GBM?

The prod models predict a Poisson mean λ that becomes P(over line) via the
Poisson CDF + isotonic calibration. So any ensemble has to stay
distribution-preserving — every member must output a λ, or the CDF step breaks.
That rules out blending in an L1/quantile point-estimator (it isn't a λ). The two
ensembles worth testing under that constraint, both decided WITHOUT peeking at
test:

  seedbag  — average of K Poisson GBMs with varied seed + feature_fraction
             (pure variance reduction; the blended mean is still a valid λ)
  glmblend — w·GBM + (1−w)·sklearn PoissonRegressor (a linear Poisson GLM, whose
             errors are decorrelated from the tree model); w tuned on validation

Ship gate = +0.5% test MAE (same bar as the auto-retrain A/B gate). As of
2026-06, every prod stat came back under it — the single calibrated GBM is at the
MAE floor and stacking adds <0.25%, not worth the multi-model inference +
recalibration burden. (The ensemble that DOES pay off, model⊕market blending, is
already shipped in blend_weights.py.) Re-run as data grows or new markets land.

Run:  python -m props.models.ensemble_eval                 (all stats)
      python -m props.models.ensemble_eval --stat "nba points"
"""
from __future__ import annotations

import argparse

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor
from sklearn.preprocessing import StandardScaler

from props.models import hits_v1, total_bases_v1, nba_points_v1, nba_assists_v1
from props.utils.logging import log, configure_logging

# stat label -> (trainer module, train/test cutoff, inner-val window in days)
SPECS = {
    "mlb hits":        (hits_v1,        "2025-01-01", 30),
    "mlb total_bases": (total_bases_v1, "2025-01-01", 30),
    "nba points":      (nba_points_v1,  "2026-01-01", 21),
    "nba assists":     (nba_assists_v1, "2026-01-01", 21),
}
GATE = 0.5  # % test-MAE improvement required to be worth shipping an ensemble
GBM = {"objective": "poisson", "learning_rate": 0.04, "num_leaves": 31,
       "min_data_in_leaf": 50, "feature_fraction": 0.9, "bagging_fraction": 0.9,
       "bagging_freq": 5, "verbose": -1}


def _train_gbm(fit, val, keys, seed, ff=0.9):
    params = {**GBM, "seed": seed, "feature_fraction": ff}
    return lgb.train(params, lgb.Dataset(fit[keys], fit["y"]), num_boost_round=2000,
                     valid_sets=[lgb.Dataset(val[keys], val["y"])],
                     callbacks=[lgb.early_stopping(50, verbose=False)])


def _mae(p, y):
    return float(np.mean(np.abs(p - y)))


def eval_stat(label: str) -> dict:
    mod, split, valdays = SPECS[label]
    df = mod.load_training_data().sort_values("game_date")
    keys = mod.FEATURE_KEYS
    tr = df[df.game_date < pd.Timestamp(split)]
    te = df[df.game_date >= pd.Timestamp(split)]
    vc = tr["game_date"].max() - pd.Timedelta(days=valdays)
    fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
    yte, yval = te["y"].to_numpy(float), val["y"].to_numpy(float)

    m0 = _train_gbm(fit, val, keys, seed=42)
    p_single = m0.predict(te[keys], num_iteration=m0.best_iteration)

    # seedbag — average of seed/feature_fraction-varied Poisson GBMs
    preds = []
    for s, ff in [(1, 0.8), (2, 0.9), (3, 0.7), (4, 0.85), (5, 0.95)]:
        m = _train_gbm(fit, val, keys, seed=s, ff=ff)
        preds.append(m.predict(te[keys], num_iteration=m.best_iteration))
    p_bag = np.mean(preds, axis=0)

    # glmblend — GBM + linear Poisson GLM, weight chosen on validation only
    sc = StandardScaler().fit(fit[keys])
    glm = PoissonRegressor(alpha=1.0, max_iter=500).fit(sc.transform(fit[keys]), fit["y"])
    m0_val = m0.predict(val[keys], num_iteration=m0.best_iteration)
    p_glm_val = glm.predict(sc.transform(val[keys]))
    ws = np.linspace(0, 1, 21)
    w = float(ws[np.argmin([_mae(x * m0_val + (1 - x) * p_glm_val, yval) for x in ws])])
    p_blend = w * p_single + (1 - w) * glm.predict(sc.transform(te[keys]))

    base = _mae(p_single, yte)
    res = {"label": label, "n_test": len(te), "single": base,
           "seedbag_pct": 100 * (base - _mae(p_bag, yte)) / base,
           "glmblend_w": w, "glmblend_pct": 100 * (base - _mae(p_blend, yte)) / base}
    log.info("ensemble_eval", **{k: (round(v, 3) if isinstance(v, float) else v)
                                  for k, v in res.items()})
    return res


def run(stats: list[str]):
    configure_logging()
    for label in stats:
        r = eval_stat(label)
        print(f"\n### {r['label']}  (n_test={r['n_test']})  single MAE {r['single']:.4f}")
        for name, pct in [("seedbag(5)", r["seedbag_pct"]),
                          (f"glmblend(w={r['glmblend_w']:.2f})", r["glmblend_pct"])]:
            verdict = "SHIP" if pct >= GATE else "no gain"
            print(f"   {name:22} {pct:+.2f}%  -> {verdict}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stat", choices=list(SPECS), help="default: all")
    args = p.parse_args()
    run([args.stat] if args.stat else list(SPECS))


if __name__ == "__main__":
    main()
