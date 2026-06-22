"""Train CBB player-prop models: points/rebounds/assists/threes + the 4 combos.

One parameterized Poisson trainer over the core cbb_rolling features (the advanced/
IQ features turned out neutral elsewhere, so CBB ships on the rolling core). Singles
use the box-score stat as target; combos use the summed components. Each is scored
vs its season-avg baseline and only the winners are kept.

    python -m props.models.cbb_models_v1
"""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from props.utils.db import engine
from props.utils.logging import log, configure_logging

MODEL_DIR = Path("models"); MODEL_DIR.mkdir(exist_ok=True)

SINGLES = ["points", "rebounds", "assists", "threes_made"]
COMBOS = {
    "pts_rebs_asts": ["points", "rebounds", "assists"],
    "pts_rebs":      ["points", "rebounds"],
    "pts_asts":      ["points", "assists"],
    "rebs_asts":     ["rebounds", "assists"],
}
RAW = ["points", "rebounds", "assists", "threes_made"]

FEATURE_KEYS = [
    "last_5_avg_points", "last_10_avg_points", "last_20_avg_points", "season_avg_points",
    "last_5_avg_rebounds", "last_10_avg_rebounds", "last_20_avg_rebounds", "season_avg_rebounds",
    "last_5_avg_assists", "last_10_avg_assists", "last_20_avg_assists", "season_avg_assists",
    "last_5_avg_threes_made", "last_10_avg_threes_made", "season_avg_threes_made",
    "last_5_avg_minutes", "last_10_avg_minutes", "last_20_avg_minutes", "season_avg_minutes",
    "last_10_avg_fg_attempted", "last_10_avg_turnovers",
    "last_10_avg_steals", "last_10_avg_blocks",
    "days_rest", "games_played_season",
]


def load():
    log.info("loading_cbb_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code='cbb' AND pg.minutes_played >= 5
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    derived = pd.json_normalize(df["derived"]); stats = pd.json_normalize(df["stats"])
    out = pd.DataFrame({"player_game_id": df["player_game_id"].values, "game_date": df["game_date"].values})
    for k in FEATURE_KEYS:
        out[k] = pd.to_numeric(derived.get(k, 0), errors="coerce").fillna(0)
    for s in RAW:
        out[f"_st_{s}"] = pd.to_numeric(stats.get(s, 0), errors="coerce").fillna(0)
    out = out[out["last_10_avg_minutes"] > 0].copy()
    log.info("filtered_rows", n=len(out))
    return out


def target_baseline(out, name):
    if name in COMBOS:
        comps = COMBOS[name]
        return sum(out[f"_st_{c}"] for c in comps), sum(out[f"season_avg_{c}"] for c in comps)
    return out[f"_st_{name}"], out[f"season_avg_{name}"]


def train_one(name, out):
    df = out.copy()
    y, base = target_baseline(df, name)
    df["y"], df["baseline"] = y.astype(int), base
    cutoff = df["game_date"].quantile(0.8)
    tr, te = df[df.game_date < cutoff], df[df.game_date >= cutoff]
    vc = tr["game_date"].quantile(0.85)
    fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
    params = {"objective": "poisson", "metric": ["poisson", "mae"], "learning_rate": 0.05,
              "num_leaves": 31, "min_data_in_leaf": 50, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 5, "lambda_l2": 1.0, "verbose": -1, "seed": 42}
    model = lgb.train(params, lgb.Dataset(fit[FEATURE_KEYS], fit["y"]), num_boost_round=800,
                      valid_sets=[lgb.Dataset(val[FEATURE_KEYS], val["y"])],
                      callbacks=[lgb.early_stopping(40)])
    pred = model.predict(te[FEATURE_KEYS], num_iteration=model.best_iteration)
    yv = te["y"].values
    mae_m, mae_b = np.mean(np.abs(pred - yv)), np.mean(np.abs(te["baseline"].values - yv))
    imp = round(100 * (mae_b - mae_m) / mae_b, 2)
    mname = f"cbb_{name}_v1"
    model.save_model(str(MODEL_DIR / f"{mname}.txt"))
    meta = {"model_path": f"models/{mname}.txt", "target": name, "feature_keys": FEATURE_KEYS,
            "best_iteration": model.best_iteration, "test_n": len(te),
            "mae_improvement_pct": imp, "trained_date": date.today().isoformat()}
    with open(MODEL_DIR / f"{mname}_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    log.info("cbb_trained", name=mname, mae_model=round(mae_m, 4), mae_baseline=round(mae_b, 4),
             mae_improvement_pct=imp, n=len(te))
    return {"name": mname, "improvement_pct": imp, "n": len(te)}


def main():
    configure_logging()
    out = load()
    results = [train_one(n, out) for n in SINGLES + list(COMBOS)]
    print("\n=== CBB models (MAE vs season-avg baseline) ===")
    for r in sorted(results, key=lambda x: -x["improvement_pct"]):
        verdict = "SHIP" if r["improvement_pct"] > 0 else "drop (worse than baseline)"
        print(f"  {r['name']:24} {r['improvement_pct']:+6.2f}%  (n={r['n']})  -> {verdict}")


if __name__ == "__main__":
    main()
