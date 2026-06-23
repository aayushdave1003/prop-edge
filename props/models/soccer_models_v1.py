"""Train/assess soccer player-prop models: shots, shots-on-target, fouls, goals,
saves. Poisson (counts), each on its relevant volume group, scored vs the season-
avg baseline. Strong prior these are weak (low event counts + substitution noise);
the assessment decides which, if any, ship."""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.features.soccer_rolling import ROLL_STATS, WINDOWS

MODEL_DIR = Path("models"); MODEL_DIR.mkdir(exist_ok=True)

# stat -> (volume-filter feature, min season-avg)
SPECS = {
    "shots":           ("season_avg_shots", 0.5),
    "shots_on_target": ("season_avg_shots", 0.5),
    "fouls":           ("season_avg_fouls", 0.3),
    "goals":           ("season_avg_shots", 0.7),
    "saves":           ("season_avg_saves", 0.5),
}
FEATURE_KEYS = ([f"last_{w}_avg_{s}" for s in ROLL_STATS for w in WINDOWS]
                + [f"season_avg_{s}" for s in ROLL_STATS] + ["days_rest", "games_played_season"])


def load():
    df = pd.read_sql("""
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id) WHERE g.sport_code='soccer'
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    der = pd.json_normalize(df["derived"]); st = pd.json_normalize(df["stats"])
    out = pd.DataFrame({"player_game_id": df["player_game_id"].values, "game_date": df["game_date"].values})
    for k in FEATURE_KEYS:
        out[k] = pd.to_numeric(der.get(k, 0), errors="coerce").fillna(0)
    for s in set(SPECS):
        out[f"_st_{s}"] = pd.to_numeric(st.get(s, 0), errors="coerce").fillna(0)
    log.info("loaded_rows", n=len(out))
    return out


def train_one(stat, out):
    filt, minv = SPECS[stat]
    df = out[out[filt] >= minv].copy()
    df["y"], df["baseline"] = df[f"_st_{stat}"], df[f"season_avg_{stat}"]
    cutoff = df["game_date"].quantile(0.8)
    tr, te = df[df.game_date < cutoff], df[df.game_date >= cutoff]
    vc = tr["game_date"].quantile(0.85)
    fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
    params = {"objective": "poisson", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 30,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
              "lambda_l2": 1.0, "verbose": -1, "seed": 42}
    model = lgb.train(params, lgb.Dataset(fit[FEATURE_KEYS], fit["y"]), num_boost_round=600,
                      valid_sets=[lgb.Dataset(val[FEATURE_KEYS], val["y"])], callbacks=[lgb.early_stopping(40)])
    pred = model.predict(te[FEATURE_KEYS], num_iteration=model.best_iteration); y = te["y"].values
    mae_m, mae_b = np.mean(np.abs(pred - y)), np.mean(np.abs(te["baseline"].values - y))
    imp = round(100 * (mae_b - mae_m) / mae_b, 2)
    name = f"soccer_{stat}_v1"
    model.save_model(str(MODEL_DIR / f"{name}.txt"))
    json.dump({"model_path": f"models/{name}.txt", "target": stat, "feature_keys": FEATURE_KEYS,
               "best_iteration": model.best_iteration, "test_n": len(te),
               "mae_improvement_pct": imp, "trained_date": date.today().isoformat()},
              open(MODEL_DIR / f"{name}_meta.json", "w"), indent=2)
    log.info("soccer_trained", name=name, mae_improvement_pct=imp, n=len(te))
    return {"name": name, "improvement_pct": imp, "n": len(te)}


def main():
    configure_logging()
    out = load()
    results = [train_one(s, out) for s in SPECS]
    print("\n=== Soccer models (MAE vs season-avg baseline) ===")
    for r in sorted(results, key=lambda x: -x["improvement_pct"]):
        v = "SHIP" if r["improvement_pct"] > 0 else "drop (worse than baseline)"
        print(f"  {r['name']:26} {r['improvement_pct']:+6.2f}%  (n={r['n']})  -> {v}")


if __name__ == "__main__":
    main()
