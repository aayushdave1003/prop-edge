"""Fit isotonic regression calibration on the hits model's 2025 holdout.

For each common prop line (0.5, 1.5, 2.5), fits an isotonic regression
mapping raw Poisson-derived P(over) -> empirical P(over) observed in 2025.
Saves the calibrator to models/hits_v1_calibrator.pkl.

Applied at inference: raw_p_over -> calibrator -> corrected_p_over.
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


MODEL_PATH = Path("models/hits_v1.txt")
META_PATH = Path("models/hits_v1_meta.json")
CALIBRATOR_PATH = Path("models/hits_v1_calibrator.pkl")

LINES_TO_CALIBRATE = [0.5, 1.5, 2.5]


def load_test_predictions():
    """Re-score the 2025 holdout to get (raw_pred, actual) pairs."""
    log.info("loading_model")
    model = lgb.Booster(model_file=str(MODEL_PATH))
    with open(META_PATH) as f:
        meta = json.load(f)
    feature_keys = meta["feature_keys"]

    log.info("loading_2025_data")
    sql = """
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code='mlb'
          AND (pg.stats->>'plate_appearances')::int >= 3
          AND g.game_date >= '2025-01-01'
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])

    derived = pd.json_normalize(df["derived"])
    stats = pd.json_normalize(df["stats"])

    X = pd.DataFrame()
    for k in feature_keys:
        if k in derived.columns:
            X[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0)
        else:
            X[k] = 0
    X = X.astype(float)

    actual = pd.to_numeric(stats["hits"], errors="coerce").fillna(0).astype(int).values

    # Filter rows with no rolling history
    mask = X["last_10_avg_at_bats"] > 0
    X = X[mask].reset_index(drop=True)
    actual = actual[mask.values]

    log.info("scoring", n=len(X))
    pred = model.predict(X, num_iteration=model.best_iteration)
    return pred, actual


def fit_calibration(raw_pred_lambda, actual_y):
    """Fit isotonic regression per prop line."""
    calibrators = {}
    log.info("fitting_calibrators", lines=LINES_TO_CALIBRATE)

    for line in LINES_TO_CALIBRATE:
        # Raw model probability of going over this line
        raw_p_over = 1 - scipy_stats.poisson.cdf(int(line), raw_pred_lambda)
        # Actual binary outcome
        actual_over = (actual_y > line).astype(int)

        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
        iso.fit(raw_p_over, actual_over)
        calibrators[line] = iso

        # Audit: how big is the correction?
        sample_raw = np.array([0.2, 0.4, 0.5, 0.6, 0.7, 0.8])
        sample_cal = iso.predict(sample_raw)
        log.info("calibrator_audit", line=line,
                 raw_to_calibrated=[
                     f"{r:.2f}->{c:.2f}"
                     for r, c in zip(sample_raw, sample_cal)
                 ])

    return calibrators


def save_calibrators(calibrators):
    with open(CALIBRATOR_PATH, "wb") as f:
        pickle.dump(calibrators, f)
    log.info("calibrators_saved", path=str(CALIBRATOR_PATH))


def main():
    configure_logging()
    raw_pred, actual = load_test_predictions()
    calibrators = fit_calibration(raw_pred, actual)
    save_calibrators(calibrators)

    print("\n=== Calibration summary ===")
    for line in LINES_TO_CALIBRATE:
        raw_p = 1 - scipy_stats.poisson.cdf(int(line), raw_pred)
        cal_p = calibrators[line].predict(raw_p)
        actual_over = (actual > line).astype(int)
        print(f"\nLine {line}: {len(raw_p)} samples")
        for bin_low, bin_high in [(0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]:
            mask = (raw_p >= bin_low) & (raw_p < bin_high)
            if mask.sum() == 0:
                continue
            avg_raw = raw_p[mask].mean()
            avg_cal = cal_p[mask].mean()
            actual_rate = actual_over[mask].mean()
            print(f"  raw in [{bin_low:.1f},{bin_high:.1f}): n={mask.sum():>5d}, "
                  f"raw_avg={avg_raw:.3f}, calibrated={avg_cal:.3f}, actual={actual_rate:.3f}")


if __name__ == "__main__":
    main()
