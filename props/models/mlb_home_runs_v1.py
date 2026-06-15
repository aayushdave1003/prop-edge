"""Train MLB home_runs prediction model (batter).

Binary classifier: predicts P(home_runs >= 1) directly.
Poisson objective failed (best_iter=1) because HRs are too sparse —
binary cross-entropy works much better for rare events.
Output is P(HR=0.5 OVER) used directly in score_and_edge.
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
MODEL_PATH = MODEL_DIR / "mlb_home_runs_v1.txt"
META_PATH  = MODEL_DIR / "mlb_home_runs_v1_meta.json"

TARGET = "home_runs"

FEATURE_KEYS = [
    # HR history (strongest signals)
    "last_5_avg_home_runs",
    "last_10_avg_home_runs",
    "last_20_avg_home_runs",
    "season_avg_home_runs",
    "last_10_rate_over_0.5_home_runs",
    # Power proxies
    "season_avg_batter_iso",
    "season_avg_batter_slg",
    "season_avg_batter_hard_contact",
    "last_5_avg_total_bases",
    "last_10_avg_total_bases",
    "season_avg_total_bases",
    # Plate appearances (exposure)
    "last_10_avg_at_bats",
    "season_avg_at_bats",
    "last_10_avg_walks",
    # Pitcher matchup
    "pitcher_last_10_era",
    "pitcher_last_5_era",
    "pitcher_last_10_k_rate",
    "pitcher_last_10_h_per_9",
    "pitcher_last_10_bb_per_9",
    # Batter handedness vs pitcher
    "platoon_advantage",
    # Park + recency
    "park_factor",
    "days_rest",
    "games_played_season",
    # NB: weather features were A/B-tested here and HURT the sparse HR classifier
    # (-7.7% MAE), so they're deliberately excluded — total_bases/hits keep them.
]


def load_training_data():
    log.info("loading_mlb_hr_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season,
               pg.derived, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'at_bats')::numeric > 0
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("raw_rows", n=len(df))

    derived = pd.json_normalize(df["derived"])
    stats   = pd.json_normalize(df["stats"])

    y_raw = pd.to_numeric(stats.get(TARGET, 0), errors="coerce").fillna(0)
    out = pd.DataFrame({
        "player_game_id": df["player_game_id"].values,
        "player_id":      df["player_id"].values,
        "game_date":      df["game_date"].values,
        "season":         df["season"].values,
        "y": (y_raw >= 1).astype(int).values,  # binary: did the batter hit any HR?
        "season_avg_home_runs": pd.to_numeric(derived.get("season_avg_home_runs", 0), errors="coerce").fillna(0).values,
    })
    for k in FEATURE_KEYS:
        if k in derived.columns:
            out[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0)
        else:
            out[k] = 0

    out = out[out["last_10_avg_at_bats"] > 0].copy()
    log.info("filtered_rows", n=len(out))
    return out


def train_model(train_df, val_df):
    lgb_train = lgb.Dataset(train_df[FEATURE_KEYS], train_df["y"])
    lgb_val   = lgb.Dataset(val_df[FEATURE_KEYS], val_df["y"], reference=lgb_train)
    params = {
        "objective": "binary", "metric": ["binary_logloss", "auc"],
        "learning_rate": 0.04, "num_leaves": 31, "min_data_in_leaf": 100,
        "feature_fraction": 0.8, "bagging_fraction": 0.9, "bagging_freq": 5,
        "lambda_l1": 0.3, "lambda_l2": 0.5,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(params, lgb_train, num_boost_round=1000,
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
    from sklearn.metrics import roc_auc_score, log_loss
    auc = roc_auc_score(y_test, pred)
    ll  = log_loss(y_test, pred)
    # Baseline: always predict season HR rate
    base_prob = y_test.mean()
    ll_base = log_loss(y_test, [base_prob] * len(y_test))
    log.info("test_metrics", auc=round(auc, 4), logloss=round(ll, 4),
             logloss_baseline=round(ll_base, 4),
             logloss_improvement_pct=round(100 * (ll_base - ll) / ll_base, 2))
    from props.models.retrain_log import log_retrain_run
    log_retrain_run("mlb_home_runs_v1", "mlb", df["game_date"].min().date(),
                    len(test_df), 100 * (ll_base - ll) / ll_base if ll_base else None)

    model.save_model(str(MODEL_PATH))
    meta = {"model_path": str(MODEL_PATH), "target": TARGET, "feature_keys": FEATURE_KEYS,
            "best_iteration": model.best_iteration, "train_n": len(fit_df),
            "val_n": len(val_df), "test_n": len(test_df),
            "prediction_distribution": "binary",
            "trained_date": date.today().isoformat()}
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    log.info("model_saved", path=str(MODEL_PATH))


if __name__ == "__main__":
    main()
