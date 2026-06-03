"""Train and evaluate a pitcher strikeouts prediction model.

Predicts expected K count for a starting pitcher given features from
player_games.derived. Uses LightGBM with Poisson objective.

Time-based train/test split: train on games before 2025, test on 2025+.
"""
import json
import os
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import text
from scipy import stats as scipy_stats
import joblib

from props.utils.db import engine
from props.utils.logging import log, configure_logging


MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODEL_DIR / "strikeouts_v1.txt"
META_PATH = MODEL_DIR / "strikeouts_v1_meta.json"

TARGET = "strikeouts_pitcher"

# Features used as inputs. These all exist in player_games.derived after
# running mlb_rolling.py and mlb_opposing_lineup.py.
FEATURE_KEYS = [
    # Pitcher's own rolling K performance
    "last_5_avg_strikeouts_pitcher",
    "last_10_avg_strikeouts_pitcher",
    "last_20_avg_strikeouts_pitcher",
    "season_avg_strikeouts_pitcher",
    "last_10_rate_over_4.5_strikeouts_pitcher",
    "last_10_rate_over_5.5_strikeouts_pitcher",
    "last_10_rate_over_6.5_strikeouts_pitcher",
    "last_10_rate_over_7.5_strikeouts_pitcher",
    # Pitcher's other rolling stats
    "last_5_avg_outs_recorded",
    "last_10_avg_outs_recorded",
    "last_20_avg_outs_recorded",
    "season_avg_outs_recorded",
    "last_5_avg_batters_faced",
    "last_10_avg_batters_faced",
    "last_20_avg_batters_faced",
    "last_5_avg_hits_allowed",
    "last_10_avg_hits_allowed",
    "last_5_avg_walks_allowed",
    "last_10_avg_walks_allowed",
    "last_5_avg_earned_runs",
    "last_10_avg_earned_runs",
    "season_avg_earned_runs",
    # Workload context
    "days_rest",
    "games_played_season",
    # Opposing lineup quality
    "lineup_last_10_k_rate",
    "lineup_last_20_k_rate",
    "lineup_last_10_avg_runs",
    "lineup_last_20_avg_runs",
    "lineup_last_10_avg_tb",
    "lineup_last_10_walk_rate",
    # Advanced pitcher metrics
    "last_5_avg_pitcher_bb9",
    "last_10_avg_pitcher_bb9",
    "last_5_avg_pitcher_hr9",
    "last_10_avg_pitcher_hr9",
    "last_5_avg_pitcher_pitch_eff",
    "last_10_avg_pitcher_pitch_eff",
    "last_5_avg_pitcher_command",
    "last_10_avg_pitcher_command",
    "season_avg_pitcher_command",
    "last_10_avg_pitcher_qs_rate",
    "park_factor",
]


def load_training_data() -> pd.DataFrame:
    """Pull all starter-games with their features and target."""
    log.info("loading_training_data")
    sql = """
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season,
               pg.derived, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'batters_faced')::int >= 15
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("raw_starts", n=len(df))

    derived = pd.json_normalize(df["derived"])
    stats = pd.json_normalize(df["stats"])

    out = pd.DataFrame({
        "player_game_id": df["player_game_id"].values,
        "player_id": df["player_id"].values,
        "game_date": df["game_date"].values,
        "season": df["season"].values,
        "y": pd.to_numeric(stats[TARGET], errors="coerce").fillna(0).astype(int).values,
    })
    for k in FEATURE_KEYS:
        if k in derived.columns:
            out[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0)
        else:
            out[k] = 0
    # Drop rows where the pitcher has no rolling history — those features all zero
    # and there's nothing to learn from.
    out = out[out["last_10_avg_strikeouts_pitcher"] > 0].copy()
    log.info("filtered_starts", n=len(out))
    return out


def split_train_test(df: pd.DataFrame):
    train = df[df["game_date"] < pd.Timestamp("2025-01-01")].copy()
    test = df[df["game_date"] >= pd.Timestamp("2025-01-01")].copy()
    log.info("split", train=len(train), test=len(test),
             train_date_range=(train["game_date"].min().date().isoformat(),
                               train["game_date"].max().date().isoformat()),
             test_date_range=(test["game_date"].min().date().isoformat(),
                              test["game_date"].max().date().isoformat()))
    return train, test


def train_model(train_df, val_df):
    X_train = train_df[FEATURE_KEYS]
    y_train = train_df["y"]
    X_val = val_df[FEATURE_KEYS]
    y_val = val_df["y"]

    lgb_train = lgb.Dataset(X_train, y_train)
    lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train)

    params = {
        "objective": "poisson",
        "metric": ["poisson", "mae", "rmse"],
        "learning_rate": 0.04,
        "num_leaves": 31,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 5,
        "verbose": -1,
        "seed": 42,
    }

    log.info("training_lgb")
    model = lgb.train(
        params,
        lgb_train,
        num_boost_round=2000,
        valid_sets=[lgb_train, lgb_val],
        valid_names=["train", "val"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=50),
            lgb.log_evaluation(period=100),
        ],
    )
    log.info("trained", best_iter=model.best_iteration)
    return model


def evaluate(model, test_df):
    X_test = test_df[FEATURE_KEYS]
    y_test = test_df["y"].values

    pred = model.predict(X_test, num_iteration=model.best_iteration)
    baseline = test_df["season_avg_strikeouts_pitcher"].values

    mae_model = np.mean(np.abs(pred - y_test))
    mae_baseline = np.mean(np.abs(baseline - y_test))
    rmse_model = np.sqrt(np.mean((pred - y_test) ** 2))
    rmse_baseline = np.sqrt(np.mean((baseline - y_test) ** 2))

    log.info("test_metrics",
             mae_model=round(mae_model, 4),
             mae_baseline=round(mae_baseline, 4),
             rmse_model=round(rmse_model, 4),
             rmse_baseline=round(rmse_baseline, 4),
             mae_improvement_pct=round(
                 100 * (mae_baseline - mae_model) / mae_baseline, 2))

    # Calibration check at common prop lines
    print("\n=== Calibration: model says X% over Y.5 K, actual rate ===")
    for line in [4.5, 5.5, 6.5, 7.5, 8.5]:
        # Predicted P(K > line) under Poisson assumption with predicted mean
        p_over = 1 - scipy_stats.poisson.cdf(int(line), pred)
        # Bin by predicted probability and compute empirical rate
        bins = [0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]
        df = pd.DataFrame({"p": p_over, "actual": (y_test > line).astype(int)})
        df["bin"] = pd.cut(df["p"], bins=bins, include_lowest=True)
        cal = df.groupby("bin", observed=True).agg(
            n=("actual", "count"),
            avg_pred=("p", "mean"),
            actual_rate=("actual", "mean"),
        ).round(3)
        print(f"\nLine {line}:")
        print(cal)

    return pred


def feature_importance(model):
    imp = pd.DataFrame({
        "feature": model.feature_name(),
        "importance": model.feature_importance(importance_type="gain"),
    }).sort_values("importance", ascending=False)
    print("\n=== Top 15 features by gain ===")
    print(imp.head(15).to_string(index=False))
    return imp


def main():
    configure_logging()
    df = load_training_data()
    train_df, test_df = split_train_test(df)

    # Within train, hold out the last month for early-stopping validation
    val_cutoff = train_df["game_date"].max() - pd.Timedelta(days=30)
    fit_df = train_df[train_df["game_date"] < val_cutoff]
    val_df = train_df[train_df["game_date"] >= val_cutoff]
    log.info("inner_split", fit=len(fit_df), val=len(val_df))

    model = train_model(fit_df, val_df)
    pred = evaluate(model, test_df)
    imp = feature_importance(model)

    model.save_model(str(MODEL_PATH))
    meta = {
        "model_path": str(MODEL_PATH),
        "target": TARGET,
        "feature_keys": FEATURE_KEYS,
        "best_iteration": model.best_iteration,
        "train_n": len(fit_df),
        "val_n": len(val_df),
        "test_n": len(test_df),
        "trained_date": date.today().isoformat(),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    log.info("model_saved", path=str(MODEL_PATH))


if __name__ == "__main__":
    main()
