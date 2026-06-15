"""Train total bases prediction model. Poisson objective. Filter: PA >= 3."""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from props.utils.db import engine
from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)
MODEL_PATH = MODEL_DIR / "total_bases_v1.txt"
META_PATH  = MODEL_DIR / "total_bases_v1_meta.json"
TARGET = "total_bases"

FEATURE_KEYS = [
    "last_5_avg_total_bases","last_10_avg_total_bases","last_20_avg_total_bases","season_avg_total_bases",
    "last_10_rate_over_1.5_total_bases","last_10_rate_over_2.5_total_bases","last_10_rate_over_3.5_total_bases",
    "last_5_avg_hits","last_10_avg_hits","season_avg_hits",
    "last_5_avg_home_runs","last_10_avg_home_runs","season_avg_home_runs",
    "last_5_avg_doubles","last_10_avg_doubles",
    "last_5_avg_at_bats","last_10_avg_at_bats",
    "last_5_avg_strikeouts","last_10_avg_strikeouts",
    "last_5_avg_rbis","last_10_avg_rbis",
    "days_rest","games_played_season",
    "pitcher_last_5_k_rate","pitcher_last_10_k_rate",
    "pitcher_last_5_h_per_9","pitcher_last_10_h_per_9",
    "pitcher_last_5_era","pitcher_last_10_era",
    "pitcher_last_5_avg_outs","pitcher_last_10_avg_outs",
    "last_10_avg_batter_iso","last_10_avg_batter_slg",
    "last_10_avg_batter_hard_contact","last_10_avg_batter_hr_rate",
    "last_10_avg_batter_k_rate","season_avg_batter_hard_contact",
    "last_10_avg_pitcher_command","last_10_avg_pitcher_hr9","park_factor",
    "wx_temp", "wx_wind_out",   # ballpark weather — wind out drives offense
]

def load_training_data():
    log.info("loading_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season,
               pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code='mlb' AND (pg.stats->>'plate_appearances')::int >= 3
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    derived = pd.json_normalize(df["derived"])
    stats   = pd.json_normalize(df["stats"])
    out = pd.DataFrame({
        "player_game_id": df["player_game_id"].values,
        "player_id": df["player_id"].values,
        "game_date": df["game_date"].values,
        "season": df["season"].values,
        "y": pd.to_numeric(stats[TARGET], errors="coerce").fillna(0).astype(int).values,
    })
    for k in FEATURE_KEYS:
        out[k] = pd.to_numeric(derived.get(k, pd.Series(0, index=derived.index)), errors="coerce").fillna(0)
    out = out[out["last_10_avg_at_bats"] > 0].copy()
    log.info("filtered_rows", n=len(out))
    return out

def split_train_test(df):
    return df[df["game_date"] < pd.Timestamp("2025-01-01")].copy(), df[df["game_date"] >= pd.Timestamp("2025-01-01")].copy()

def train_model(train_df, val_df):
    import os
    from props.models.train_weights import recency_weights
    w = recency_weights(train_df["game_date"])
    params = {"objective":"poisson","metric":["poisson","mae"],"learning_rate":0.04,
              "num_leaves":31,"min_data_in_leaf":100,"feature_fraction":0.9,
              "bagging_fraction":0.9,"bagging_freq":5,"verbose":-1,"seed":42}
    if os.environ.get("HP_TUNE"):   # opt-in Optuna search (retrain_and_promote --tune)
        from props.models.tune import tune_lgb
        params.update(tune_lgb(train_df, val_df, FEATURE_KEYS, objective="poisson", weight=w))
    lgb_train = lgb.Dataset(train_df[FEATURE_KEYS], train_df["y"], weight=w)
    lgb_val   = lgb.Dataset(val_df[FEATURE_KEYS], val_df["y"], reference=lgb_train)
    model = lgb.train(params, lgb_train, num_boost_round=2000,
                      valid_sets=[lgb_train, lgb_val], valid_names=["train","val"],
                      callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)])
    log.info("trained", best_iter=model.best_iteration)
    return model

def evaluate(model, test_df):
    pred = model.predict(test_df[FEATURE_KEYS], num_iteration=model.best_iteration)
    y    = test_df["y"].values
    baseline = test_df["season_avg_total_bases"].values
    mae_m, mae_b = np.mean(np.abs(pred-y)), np.mean(np.abs(baseline-y))
    log.info("test_metrics", mae_model=round(mae_m,4), mae_baseline=round(mae_b,4),
             mae_improvement_pct=round(100*(mae_b-mae_m)/mae_b,2))
    return pred, mae_m, mae_b

def main():
    configure_logging()
    df = load_training_data()
    train_df, test_df = split_train_test(df)
    val_cutoff = train_df["game_date"].max() - pd.Timedelta(days=30)
    fit_df = train_df[train_df["game_date"] < val_cutoff]
    val_df = train_df[train_df["game_date"] >= val_cutoff]
    model = train_model(fit_df, val_df)
    _pred, _maem, _maeb = evaluate(model, test_df)
    from props.models.retrain_log import log_retrain_run
    log_retrain_run("total_bases_v1", "mlb", df["game_date"].min().date(),
                    len(test_df), 100 * (_maeb - _maem) / _maeb if _maeb else None)
    model.save_model(str(MODEL_PATH))
    meta = {"model_path":str(MODEL_PATH),"target":TARGET,"feature_keys":FEATURE_KEYS,
            "best_iteration":model.best_iteration,"train_n":len(fit_df),
            "val_n":len(val_df),"test_n":len(test_df),"trained_date":date.today().isoformat()}
    with open(META_PATH,"w") as f: json.dump(meta,f,indent=2)
    log.info("model_saved", path=str(MODEL_PATH))

if __name__ == "__main__":
    main()
