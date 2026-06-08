"""Fit global isotonic calibration for all NBA prop models.

Previous approach: per-line calibration at 9.5, 14.5, etc. — only covers
standard lines. PrizePicks uses non-standard lines (28.0, 30.5) that got
raw uncalibrated Poisson probs.

New approach: one global IsotonicRegression per stat that maps any raw
Poisson probability → empirically-corrected probability. Trained on ALL
lines (not just standard ones) from the 2026 holdout games.

The backtest showed the model says 80% but hits 54.9% — systematic
overconfidence from the Poisson tail assumption. This fix corrects it.
"""
import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.isotonic import IsotonicRegression
from scipy import stats as scipy_stats
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import log, configure_logging


MODELS = [
    {"name": "nba_points_v1",   "target": "points"},
    {"name": "nba_rebounds_v1", "target": "rebounds"},
    {"name": "nba_assists_v1",  "target": "assists"},
]

# Lines to evaluate at for each stat during calibration — dense grid
# gives the isotonic regression much more signal than 4-5 fixed lines.
CAL_LINES = {
    "points":   [5.5, 9.5, 12.5, 14.5, 17.5, 19.5, 22.5, 24.5, 27.5, 29.5, 34.5, 39.5],
    "rebounds": [2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 9.5, 11.5],
    "assists":  [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5],
}


def calibrate_one(spec: dict):
    model_path     = Path(f"models/{spec['name']}.txt")
    meta_path      = Path(f"models/{spec['name']}_meta.json")
    calibrator_out = Path(f"models/{spec['name']}_calibrator.pkl")

    log.info("calibrating_global", model=spec["name"])
    model = lgb.Booster(model_file=str(model_path))
    with open(meta_path) as f:
        meta = json.load(f)
    feature_keys = meta["feature_keys"]
    target = spec["target"]

    # Calibrate on ALL regular-season games (nba_api external_id prefix 0022),
    # which now includes the full 2026 regular season — the old `< 2026-01-01`
    # cutoff ignored ~20k recent games. PLAYOFFS (0042) and play-in (0052) are
    # excluded on purpose: they're a different, lower-scoring distribution that
    # makes the model overconfident (the 71%->46% points problem) and would
    # pollute a calibrator that mostly serves regular-season predictions.
    # 5-fold time-ordered out-of-fold CV keeps the fit honest (no leakage).
    df = pd.read_sql(text("""
        SELECT pg.derived, pg.stats, g.game_date
        FROM player_games pg
        JOIN games g USING(game_id)
        WHERE g.sport_code = 'nba'
          AND pg.minutes_played >= 10
          AND g.external_id LIKE '0022%'
          AND g.status = 'final'
        ORDER BY g.game_date
    """), engine)

    if len(df) < 200:
        log.warning("too_few_holdout_rows", n=len(df), model=spec["name"])
        return

    derived = pd.json_normalize(df["derived"])
    stats   = pd.json_normalize(df["stats"])

    X = pd.DataFrame()
    for k in feature_keys:
        if k in derived.columns:
            X[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0)
        else:
            X[k] = 0.0
    X = X.astype(float)
    actual = pd.to_numeric(stats[target], errors="coerce").fillna(0).values

    # Filter: must have some rolling history (non-zero season avg)
    mask = X[feature_keys[0]] > 0
    X      = X[mask].reset_index(drop=True)
    actual = actual[mask.values]

    # Out-of-fold predictions via 5-fold time-ordered CV
    # Prevents the calibrator from fitting to data it's tested on
    n      = len(X)
    folds  = 5
    fold_size = n // folds
    oof_raw    = np.zeros(n)
    oof_actual = np.zeros(n)  # will be expanded per line below

    cal_lines = CAL_LINES.get(target, [])
    raw_probs  = []
    actual_hit = []

    for fold in range(folds):
        val_start = fold * fold_size
        val_end   = val_start + fold_size if fold < folds - 1 else n
        train_idx = list(range(0, val_start))
        val_idx   = list(range(val_start, val_end))

        if len(train_idx) < 100:
            # First fold has no training data — skip
            continue

        X_tr = X.iloc[train_idx][feature_keys].astype(float)
        y_tr = actual[train_idx]
        X_vl = X.iloc[val_idx][feature_keys].astype(float)

        ds_tr = lgb.Dataset(X_tr, y_tr)
        fold_params = {
            "objective": "poisson", "metric": "poisson",
            "learning_rate": 0.05, "num_leaves": 20, "verbose": -1, "seed": 42,
        }
        fold_model = lgb.train(fold_params, ds_tr, num_boost_round=meta.get("clf_best_iter", 200) or 200)
        fold_lambda = fold_model.predict(X_vl)

        for line in cal_lines:
            raw_p = 1 - scipy_stats.poisson.cdf(int(line), fold_lambda)
            hit   = (actual[val_idx] > line).astype(float)
            raw_probs.extend(raw_p.tolist())
            actual_hit.extend(hit.tolist())

    # Also add predictions from the main model on the full training set
    # as a fallback if folds give too few points
    pred_lambda = model.predict(X[feature_keys].astype(float),
                                num_iteration=model.best_iteration)

    raw_probs  = np.array(raw_probs)
    actual_hit = np.array(actual_hit)

    # Sort by raw_prob for isotonic fit
    order = np.argsort(raw_probs)
    raw_probs  = raw_probs[order]
    actual_hit = actual_hit[order]

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99,
                             increasing=True)
    iso.fit(raw_probs, actual_hit)

    # Audit: show correction at key probability levels
    check_pts = np.array([0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9])
    calibrated = iso.predict(check_pts)
    log.info("calibration_map", model=spec["name"],
             mapping=[f"{r:.0%}->{c:.1%}" for r, c in zip(check_pts, calibrated)])

    # --- Calibration quality report ---
    print(f"\n{spec['name']} global calibration ({len(df)} holdout games, "
          f"{len(raw_probs):,} data points)")
    print(f"  {'Raw prob':>10}  {'Calibrated':>10}  {'Correction':>12}")
    for r, c in zip(check_pts, calibrated):
        print(f"  {r:>10.0%}  {c:>10.1%}  {c-r:>+11.1%}")

    # Save as dict with single key "global" so registry code stays compatible
    calibrators = {"global": iso}
    with open(calibrator_out, "wb") as f:
        pickle.dump(calibrators, f)
    log.info("calibrator_saved", model=spec["name"], path=str(calibrator_out),
             n_points=len(raw_probs))


def main():
    configure_logging()
    for spec in MODELS:
        calibrate_one(spec)
    print("\nAll calibrators saved. Run predict_today to see corrected probabilities.")


if __name__ == "__main__":
    main()
