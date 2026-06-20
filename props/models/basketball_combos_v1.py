"""Train NBA + WNBA combo prop models: pts_rebs_asts / pts_rebs / pts_asts / rebs_asts.

Same lesson as hits_runs_rbis — a direct Poisson model on the summed target beats
summing component models. One parameterized trainer over sport x combo; each fits
on the rolling points/rebounds/assists + minutes/usage history (the model picks
which matter per combo). Filter: rotation players (last_10_avg_minutes > 0).

    python -m props.models.basketball_combos_v1                 # train all 8
    python -m props.models.basketball_combos_v1 --sport wnba    # one sport
"""
import argparse
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import text
from props.utils.db import engine
from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models"); MODEL_DIR.mkdir(exist_ok=True)

COMBOS = {
    "pts_rebs_asts": ["points", "rebounds", "assists"],
    "pts_rebs":      ["points", "rebounds"],
    "pts_asts":      ["points", "assists"],
    "rebs_asts":     ["rebounds", "assists"],
}

FEATURE_KEYS = [
    "last_5_avg_points", "last_10_avg_points", "last_20_avg_points", "season_avg_points",
    "last_5_avg_rebounds", "last_10_avg_rebounds", "last_20_avg_rebounds", "season_avg_rebounds",
    "last_5_avg_assists", "last_10_avg_assists", "last_20_avg_assists", "season_avg_assists",
    "last_5_avg_minutes", "last_10_avg_minutes", "last_20_avg_minutes", "season_avg_minutes",
    "last_10_avg_usage_rate", "season_avg_usage_rate",
    "last_10_avg_fg_attempted", "last_10_avg_turnovers",
    "days_rest", "games_played_season",
]


def load_sport(sport: str) -> pd.DataFrame:
    log.info("loading_combo_training_data", sport=sport)
    df = pd.read_sql(text("""
        SELECT pg.player_game_id, pg.player_id, g.game_date, g.season, pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code = :sport AND pg.minutes_played >= 5
    """), engine, params={"sport": sport})
    df["game_date"] = pd.to_datetime(df["game_date"])
    derived = pd.json_normalize(df["derived"])
    stats   = pd.json_normalize(df["stats"])
    base = pd.DataFrame({
        "player_game_id": df["player_game_id"].values,
        "game_date": df["game_date"].values,
    })
    for k in FEATURE_KEYS:
        base[k] = pd.to_numeric(derived.get(k, 0), errors="coerce").fillna(0)
    for comp in ("points", "rebounds", "assists"):
        base[comp] = pd.to_numeric(stats.get(comp, 0), errors="coerce").fillna(0)
    base = base[base["last_10_avg_minutes"] > 0].copy()
    log.info("filtered_rows", sport=sport, n=len(base))
    return base


def train_combo(sport: str, combo: str, comps: list, base: pd.DataFrame) -> dict:
    df = base.copy()
    df["y"] = sum(df[c] for c in comps).astype(int)
    df["baseline"] = sum(df[f"season_avg_{c}"] for c in comps)
    cutoff = df["game_date"].quantile(0.8)
    train_df, test_df = df[df["game_date"] < cutoff], df[df["game_date"] >= cutoff]
    val_cut = train_df["game_date"].quantile(0.85)
    fit_df, val_df = train_df[train_df["game_date"] < val_cut], train_df[train_df["game_date"] >= val_cut]

    params = {"objective": "poisson", "metric": ["poisson", "mae"], "learning_rate": 0.05,
              "num_leaves": 15, "min_data_in_leaf": 20, "lambda_l2": 1.0,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
              "verbose": -1, "seed": 42}
    model = lgb.train(params, lgb.Dataset(fit_df[FEATURE_KEYS], fit_df["y"]),
                      num_boost_round=500,
                      valid_sets=[lgb.Dataset(val_df[FEATURE_KEYS], val_df["y"])],
                      callbacks=[lgb.early_stopping(30)])
    pred = model.predict(test_df[FEATURE_KEYS], num_iteration=model.best_iteration)
    y = test_df["y"].values
    mae_m, mae_b = np.mean(np.abs(pred - y)), np.mean(np.abs(test_df["baseline"].values - y))
    imp = round(100 * (mae_b - mae_m) / mae_b, 2)

    name = f"{sport}_{combo}_v1"
    model.save_model(str(MODEL_DIR / f"{name}.txt"))
    meta = {"model_path": f"models/{name}.txt", "target": combo, "components": comps,
            "feature_keys": FEATURE_KEYS, "best_iteration": model.best_iteration,
            "test_n": len(test_df), "mae_improvement_pct": imp,
            "trained_date": date.today().isoformat()}
    with open(MODEL_DIR / f"{name}_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    log.info("combo_trained", name=name, mae_model=round(mae_m, 4),
             mae_baseline=round(mae_b, 4), mae_improvement_pct=imp, n=len(test_df))
    return {"name": name, "improvement_pct": imp, "n": len(test_df)}


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=["nba", "wnba"], help="default: both")
    args = ap.parse_args()
    sports = [args.sport] if args.sport else ["nba", "wnba"]
    results = []
    for sport in sports:
        base = load_sport(sport)
        for combo, comps in COMBOS.items():
            results.append(train_combo(sport, combo, comps, base))
    print("\n=== combo models (MAE vs summed season-avg baseline) ===")
    for r in sorted(results, key=lambda x: -x["improvement_pct"]):
        verdict = "SHIP" if r["improvement_pct"] > 0 else "drop (worse than baseline)"
        print(f"  {r['name']:24} {r['improvement_pct']:+6.2f}%  (n={r['n']})  -> {verdict}")


if __name__ == "__main__":
    main()
