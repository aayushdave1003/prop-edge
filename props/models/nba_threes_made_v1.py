"""Train NBA threes_made prediction model."""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb

from props.utils.db import engine
from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "nba_threes_made_v1.txt"
META_PATH  = MODEL_DIR / "nba_threes_made_v1_meta.json"

TARGET = "threes_made"

FEATURE_KEYS = [
    "last_5_avg_threes_made",
    "last_10_avg_threes_made",
    "last_20_avg_threes_made",
    "season_avg_threes_made",
    "last_5_avg_threes_attempted",
    "last_10_avg_threes_attempted",
    "last_20_avg_threes_attempted",
    "season_avg_threes_attempted",
    "last_5_avg_fg_attempted",
    "last_10_avg_fg_attempted",
    "last_5_avg_minutes",
    "last_10_avg_minutes",
    "last_20_avg_minutes",
    "season_avg_minutes",
    "last_10_avg_points",
    "last_10_avg_floor_spacing_score",
    "season_avg_floor_spacing_score",
    "last_10_avg_opp_floor_spacing",
    "last_10_avg_teammate_avg_floor_spacing",
    "days_rest",
    "games_played_season",
    "opp_last_5_allowed_threes_scored",
    "opp_last_10_allowed_threes_scored",
    "is_back_to_back",
    "team_days_rest",
    "last_10_avg_threes_made_home",
    "last_10_avg_threes_made_away",
    "min_stddev_last_10",
    "team_last_5_wins",
    "team_last_10_avg_game_total",
    "market_over_prob",
    "is_playoff",
    "series_game_num",
    "last_10_avg_usage_rate",
    "season_avg_usage_rate",
    "player_spotup_pct",
    "team_isolation_rate",
]


def load_training_data():
    log.info("loading_nba_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season,
               pg.derived, pg.stats, pg.minutes_played,
               mo.avg_market_over_prob
        FROM player_games pg
        JOIN games g USING (game_id)
        LEFT JOIN (
            SELECT game_id, player_id,
                   AVG(market_over_prob) AS avg_market_over_prob
            FROM market_odds
            WHERE stat_type = 'threes_made'
            GROUP BY game_id, player_id
        ) mo ON mo.game_id = pg.game_id AND mo.player_id = pg.player_id
        WHERE g.sport_code = 'nba'
          AND pg.minutes_played >= 10
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("raw_rows", n=len(df))

    derived = pd.json_normalize(df["derived"])
    stats   = pd.json_normalize(df["stats"])

    # NBA box scores store fg3_made; threes_made is the prop line stat name
    raw_col = "fg3_made" if "fg3_made" in stats.columns else TARGET
    out = pd.DataFrame({
        "player_game_id": df["player_game_id"].values,
        "player_id":      df["player_id"].values,
        "game_date":      df["game_date"].values,
        "season":         df["season"].values,
        "y": pd.to_numeric(stats[raw_col], errors="coerce").fillna(0).astype(int).values,
    })
    for k in FEATURE_KEYS:
        if k == "market_over_prob":
            out[k] = pd.to_numeric(df["avg_market_over_prob"], errors="coerce").fillna(0.5)
        elif k in derived.columns:
            out[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0)
        else:
            out[k] = 0

    out = out[out["last_10_avg_minutes"] > 0].copy()
    log.info("filtered_rows", n=len(out))
    return out


def train_model(train_df, val_df):
    lgb_train = lgb.Dataset(train_df[FEATURE_KEYS], train_df["y"])
    lgb_val   = lgb.Dataset(val_df[FEATURE_KEYS], val_df["y"], reference=lgb_train)
    params = {
        "objective": "poisson", "metric": ["poisson", "mae", "rmse"],
        "learning_rate": 0.04, "num_leaves": 31, "min_data_in_leaf": 100,
        "feature_fraction": 0.9, "bagging_fraction": 0.9, "bagging_freq": 5,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(params, lgb_train, num_boost_round=2000,
                      valid_sets=[lgb_train, lgb_val], valid_names=["train", "val"],
                      callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)])
    log.info("trained", best_iter=model.best_iteration)
    return model


def main():
    configure_logging()
    df = load_training_data()
    train_df = df[df["game_date"] < pd.Timestamp("2026-01-01")].copy()
    test_df  = df[df["game_date"] >= pd.Timestamp("2026-01-01")].copy()
    val_cutoff = train_df["game_date"].max() - pd.Timedelta(days=21)
    fit_df = train_df[train_df["game_date"] < val_cutoff]
    val_df = train_df[train_df["game_date"] >= val_cutoff]
    log.info("split", fit=len(fit_df), val=len(val_df), test=len(test_df))

    model = train_model(fit_df, val_df)

    y_test = test_df["y"].values
    pred   = model.predict(test_df[FEATURE_KEYS], num_iteration=model.best_iteration)
    mae_m  = np.mean(np.abs(pred - y_test))
    mae_b  = np.mean(np.abs(test_df["season_avg_threes_made"].values - y_test))
    log.info("test_metrics", mae_model=round(mae_m, 4), mae_baseline=round(mae_b, 4),
             mae_improvement_pct=round(100 * (mae_b - mae_m) / mae_b, 2))

    model.save_model(str(MODEL_PATH))
    meta = {"model_path": str(MODEL_PATH), "target": TARGET, "feature_keys": FEATURE_KEYS,
            "best_iteration": model.best_iteration, "train_n": len(fit_df),
            "val_n": len(val_df), "test_n": len(test_df), "trained_date": date.today().isoformat()}
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    log.info("model_saved", path=str(MODEL_PATH))


if __name__ == "__main__":
    main()
