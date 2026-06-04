"""Train NHL goals prediction model (skaters only).

Very small dataset (~418 rows), uses minimal features and heavy regularization.
"""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb

from props.utils.db import engine
from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "nhl_goals_v1.txt"
META_PATH  = MODEL_DIR / "nhl_goals_v1_meta.json"

TARGET = "goals"

FEATURE_KEYS = [
    "last_5_avg_goals",
    "last_10_avg_goals",
    "last_20_avg_goals",
    "season_avg_goals",
    "last_10_rate_over_0.5_goals",
    "last_10_rate_over_1.5_goals",
    "last_5_avg_shots",
    "last_10_avg_shots",
    "season_avg_shots",
    "last_5_avg_powerplay_goals",
    "last_10_avg_powerplay_goals",
    "last_5_avg_minutes",
    "last_10_avg_minutes",
    "season_avg_minutes",
    "last_10_avg_points",
    "days_rest",
    "games_played_season",
]


def load_training_data():
    log.info("loading_nhl_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season,
               pg.derived, pg.stats, pg.minutes_played, p.position
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p ON p.player_id = pg.player_id
        WHERE g.sport_code = 'nhl'
          AND p.position != 'G'
          AND pg.minutes_played >= 1
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("raw_rows", n=len(df))
    derived = pd.json_normalize(df["derived"])
    stats   = pd.json_normalize(df["stats"])

    out = pd.DataFrame({
        "player_game_id": df["player_game_id"].values,
        "player_id":      df["player_id"].values,
        "game_date":      df["game_date"].values,
        "season":         df["season"].values,
        "y": pd.to_numeric(stats.get(TARGET, 0), errors="coerce").fillna(0).astype(int).values,
    })
    for k in FEATURE_KEYS:
        out[k] = pd.to_numeric(derived.get(k, 0), errors="coerce").fillna(0)
    out = out[out["last_10_avg_minutes"] > 0].copy()
    log.info("filtered_rows", n=len(out))
    return out


def train_model(train_df, val_df):
    lgb_train = lgb.Dataset(train_df[FEATURE_KEYS], train_df["y"])
    lgb_val   = lgb.Dataset(val_df[FEATURE_KEYS], val_df["y"], reference=lgb_train)
    params = {
        "objective": "poisson", "metric": ["poisson", "mae"],
        "learning_rate": 0.05, "num_leaves": 10, "min_data_in_leaf": 8,
        "lambda_l2": 2.0, "feature_fraction": 0.8,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(params, lgb_train, num_boost_round=300,
                      valid_sets=[lgb_train, lgb_val], valid_names=["train", "val"],
                      callbacks=[lgb.early_stopping(25), lgb.log_evaluation(50)])
    log.info("trained", best_iter=model.best_iteration)
    return model


def main():
    configure_logging()
    df = load_training_data()
    cutoff = df["game_date"].quantile(0.8)
    train_df = df[df["game_date"] < cutoff].copy()
    test_df  = df[df["game_date"] >= cutoff].copy()
    val_cutoff = train_df["game_date"].quantile(0.85)
    fit_df = train_df[train_df["game_date"] < val_cutoff]
    val_df = train_df[train_df["game_date"] >= val_cutoff]
    log.info("split", fit=len(fit_df), val=len(val_df), test=len(test_df))

    model = train_model(fit_df, val_df)

    if len(test_df) > 0:
        y_test = test_df["y"].values
        pred   = model.predict(test_df[FEATURE_KEYS], num_iteration=model.best_iteration)
        mae_m  = np.mean(np.abs(pred - y_test))
        mae_b  = np.mean(np.abs(test_df["season_avg_goals"].values - y_test))
        log.info("test_metrics", mae_model=round(mae_m, 4), mae_baseline=round(mae_b, 4),
                 mae_improvement_pct=round(100 * (mae_b - mae_m) / max(mae_b, 1e-9), 2))

    model.save_model(str(MODEL_PATH))
    meta = {"model_path": str(MODEL_PATH), "target": TARGET, "feature_keys": FEATURE_KEYS,
            "best_iteration": model.best_iteration, "train_n": len(fit_df),
            "val_n": len(val_df), "test_n": len(test_df), "trained_date": date.today().isoformat()}
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    log.info("model_saved", path=str(MODEL_PATH))


if __name__ == "__main__":
    main()
