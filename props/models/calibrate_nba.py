"""Fit isotonic regression calibration for all NBA models.

For each NBA model (points, rebounds, assists), re-scores the 2026 holdout
and fits an isotonic regression at each common prop line to map raw
Poisson-derived probabilities to empirical hit rates.
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


MODELS_TO_CALIBRATE = [
    {
        "name": "nba_points_v1",
        "target": "points",
        "lines": [9.5, 14.5, 19.5, 24.5, 29.5],
    },
    {
        "name": "nba_rebounds_v1",
        "target": "rebounds",
        "lines": [3.5, 5.5, 7.5, 9.5],
    },
    {
        "name": "nba_assists_v1",
        "target": "assists",
        "lines": [2.5, 4.5, 6.5, 8.5],
    },
]


def calibrate_one(spec):
    model_path = Path(f"models/{spec["name"]}.txt")
    meta_path = Path(f"models/{spec["name"]}_meta.json")
    calibrator_path = Path(f"models/{spec["name"]}_calibrator.pkl")

    log.info("calibrating", model=spec["name"])
    model = lgb.Booster(model_file=str(model_path))
    with open(meta_path) as f:
        meta = json.load(f)
    feature_keys = meta["feature_keys"]

    sql = f"""
        SELECT pg.derived, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code='nba'
          AND pg.minutes_played >= 10
          AND g.game_date >= '2026-01-01'
    """
    df = pd.read_sql(sql, engine)
    derived = pd.json_normalize(df["derived"])
    stats = pd.json_normalize(df["stats"])

    X = pd.DataFrame()
    for k in feature_keys:
        if k in derived.columns:
            X[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0)
        else:
            X[k] = 0
    X = X.astype(float)
    actual = pd.to_numeric(stats[spec["target"]], errors="coerce").fillna(0).astype(int).values

    mask = X[feature_keys[0]] > 0  # filter rows with no rolling history
    X = X[mask].reset_index(drop=True)
    actual = actual[mask.values]

    log.info("scoring_holdout", model=spec["name"], n=len(X))
    pred = model.predict(X, num_iteration=model.best_iteration)

    calibrators = {}
    for line in spec["lines"]:
        raw_p_over = 1 - scipy_stats.poisson.cdf(int(line), pred)
        actual_over = (actual > line).astype(int)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
        iso.fit(raw_p_over, actual_over)
        calibrators[line] = iso

        # Quick audit
        sample = np.array([0.1, 0.3, 0.5, 0.7, 0.9])
        out = iso.predict(sample)
        log.info("calibrator", model=spec["name"], line=line,
                 mapping=[f"{r:.2f}->{c:.2f}" for r, c in zip(sample, out)])

    with open(calibrator_path, "wb") as f:
        pickle.dump(calibrators, f)
    log.info("saved", model=spec["name"], path=str(calibrator_path))


def main():
    configure_logging()
    for spec in MODELS_TO_CALIBRATE:
        calibrate_one(spec)


if __name__ == "__main__":
    main()
