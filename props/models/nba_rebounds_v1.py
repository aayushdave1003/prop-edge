"""Train and evaluate an NBA rebounds prediction model.

Predicts expected rebounds for an NBA player given features from
player_games.derived. LightGBM with Poisson objective.

Filter: minutes_played >= 10.
"""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import text
from scipy import stats as scipy_stats

from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.picks.backtest import run as run_backtest


MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODEL_DIR / "nba_rebounds_v1.txt"
META_PATH = MODEL_DIR / "nba_rebounds_v1_meta.json"

TARGET = "rebounds"

FEATURE_KEYS = [
    # Rebound history
    "last_5_avg_rebounds",
    "last_10_avg_rebounds",
    "last_20_avg_rebounds",
    "season_avg_rebounds",
    "last_10_rate_over_3.5_rebounds",
    "last_10_rate_over_5.5_rebounds",
    "last_10_rate_over_7.5_rebounds",
    # Off/def split
    "last_5_avg_off_rebounds",
    "last_10_avg_off_rebounds",
    "last_5_avg_def_rebounds",
    "last_10_avg_def_rebounds",
    # Minutes (dominant signal)
    "last_5_avg_minutes",
    "last_10_avg_minutes",
    "last_20_avg_minutes",
    "season_avg_minutes",
    # Position/usage proxies
    "last_10_avg_fg_attempted",
    "last_10_avg_points",
    "last_10_avg_blocks",
    "last_10_avg_personal_fouls",
    # Context
    "days_rest",
    "games_played_season",
    "opp_last_5_allowed_pts_scored",
    "opp_last_10_allowed_pts_scored",
    "opp_last_5_allowed_reb_scored",
    "opp_last_10_allowed_reb_scored",
    "opp_last_5_allowed_threes_scored",
    "opp_last_10_allowed_threes_scored",
    "opp_last_10_allowed_possessions",
    "is_back_to_back",
    "team_days_rest",
    "last_10_avg_rebounds_home",
    "last_10_avg_rebounds_away",
    "last_10_avg_minutes_home",
    "last_10_avg_minutes_away",
    "min_stddev_last_10",
    "team_last_5_wins",
    "team_won_last_game",
    "team_last_10_avg_game_total",
    "team_last_5_avg_game_total",
    "team_last_10_avg_pts_scored",
]


def load_training_data():
    log.info("loading_nba_training_data")
    sql = """
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season,
               pg.derived, pg.stats, pg.minutes_played
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
          AND pg.minutes_played >= 10
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("raw_rows", n=len(df))

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
    out = out[out["last_10_avg_minutes"] > 0].copy()
    log.info("filtered_rows", n=len(out))
    return out


def split_train_test(df):
    train = df[df["game_date"] < pd.Timestamp("2026-01-01")].copy()
    test = df[df["game_date"] >= pd.Timestamp("2026-01-01")].copy()
    log.info("split", train=len(train), test=len(test))
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
        "min_data_in_leaf": 100,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 5,
        "verbose": -1,
        "seed": 42,
    }

    log.info("training_lgb")
    model = lgb.train(
        params, lgb_train,
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
    baseline = test_df["season_avg_rebounds"].values

    mae_m = np.mean(np.abs(pred - y_test))
    mae_b = np.mean(np.abs(baseline - y_test))
    rmse_m = np.sqrt(np.mean((pred - y_test) ** 2))
    rmse_b = np.sqrt(np.mean((baseline - y_test) ** 2))

    log.info("test_metrics",
             mae_model=round(mae_m, 4), mae_baseline=round(mae_b, 4),
             rmse_model=round(rmse_m, 4), rmse_baseline=round(rmse_b, 4),
             mae_improvement_pct=round(100 * (mae_b - mae_m) / mae_b, 2))

    print("\n=== Calibration: model says X% over Y.5 rebounds, actual rate ===")
    for line in [3.5, 5.5, 7.5, 9.5]:
        p_over = 1 - scipy_stats.poisson.cdf(int(line), pred)
        bins = [0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]
        cal_df = pd.DataFrame({"p": p_over, "actual": (y_test > line).astype(int)})
        cal_df["bin"] = pd.cut(cal_df["p"], bins=bins, include_lowest=True)
        cal = cal_df.groupby("bin", observed=True).agg(
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


def main():
    configure_logging()
    df = load_training_data()
    train_df, test_df = split_train_test(df)
    val_cutoff = train_df["game_date"].max() - pd.Timedelta(days=21)
    fit_df = train_df[train_df["game_date"] < val_cutoff]
    val_df = train_df[train_df["game_date"] >= val_cutoff]
    log.info("inner_split", fit=len(fit_df), val=len(val_df))

    model = train_model(fit_df, val_df)
    evaluate(model, test_df)
    feature_importance(model)

    model.save_model(str(MODEL_PATH))
    meta = {
        "model_path": str(MODEL_PATH), "target": TARGET,
        "feature_keys": FEATURE_KEYS, "best_iteration": model.best_iteration,
        "train_n": len(fit_df), "val_n": len(val_df), "test_n": len(test_df),
        "trained_date": date.today().isoformat(),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    log.info("model_saved", path=str(MODEL_PATH))

    log.info("running_auto_backtest")
    try:
        run_backtest(sport="nba", trigger="retrain")
    except Exception as e:
        log.warning("auto_backtest_failed", error=str(e))


if __name__ == "__main__":
    main()
