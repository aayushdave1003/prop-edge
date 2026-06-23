"""Train NFL player-prop models: passing/rushing/receiving yards + receptions.

Assess-first (CBB-style): each market is trained on its RELEVANT volume group
(passing yards only on real passers, etc. — else WRs with 0 pass yards make it
trivial) and scored vs the season-avg baseline; only winners ship. Yards use an
L1 (MAE) objective; receptions are Poisson. Time-split train/test (no leakage).
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

# stat -> (lgb objective, volume-filter feature, min season-avg to be "relevant")
SPECS = {
    "passing_yards":   ("regression_l1", "season_avg_pass_attempts", 5.0),
    "rushing_yards":   ("regression_l1", "season_avg_carries",       3.0),
    "receiving_yards": ("regression_l1", "season_avg_targets",       2.0),
    "receptions":      ("poisson",       "season_avg_targets",       2.0),
}

_ROLL = ["passing_yards", "passing_tds", "completions", "pass_attempts", "interceptions",
         "rushing_yards", "rushing_tds", "carries",
         "receiving_yards", "receiving_tds", "receptions", "targets"]
FEATURE_KEYS = ([f"last_{w}_avg_{s}" for s in _ROLL for w in (3, 5, 8)]
                + [f"season_avg_{s}" for s in _ROLL]
                + ["days_rest", "games_played_season"])
RAW = ["passing_yards", "rushing_yards", "receiving_yards", "receptions"]


def load():
    log.info("loading_nfl_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id) WHERE g.sport_code='nfl'
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    der = pd.json_normalize(df["derived"]); st = pd.json_normalize(df["stats"])
    out = pd.DataFrame({"player_game_id": df["player_game_id"].values, "game_date": df["game_date"].values})
    for k in FEATURE_KEYS:
        out[k] = pd.to_numeric(der.get(k, 0), errors="coerce").fillna(0)
    for s in RAW:
        out[f"_st_{s}"] = pd.to_numeric(st.get(s, 0), errors="coerce").fillna(0)
    log.info("loaded_rows", n=len(out))
    return out


def train_one(stat, out):
    obj, filt, minv = SPECS[stat]
    df = out[out[filt] >= minv].copy()              # relevant volume group only
    df["y"], df["baseline"] = df[f"_st_{stat}"], df[f"season_avg_{stat}"]
    cutoff = df["game_date"].quantile(0.8)
    tr, te = df[df.game_date < cutoff], df[df.game_date >= cutoff]
    vc = tr["game_date"].quantile(0.85)
    fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
    params = {"objective": obj, "learning_rate": 0.05, "num_leaves": 31,
              "min_data_in_leaf": 30, "feature_fraction": 0.8, "bagging_fraction": 0.8,
              "bagging_freq": 5, "lambda_l2": 1.0, "verbose": -1, "seed": 42}
    model = lgb.train(params, lgb.Dataset(fit[FEATURE_KEYS], fit["y"]), num_boost_round=600,
                      valid_sets=[lgb.Dataset(val[FEATURE_KEYS], val["y"])],
                      callbacks=[lgb.early_stopping(40)])
    pred = model.predict(te[FEATURE_KEYS], num_iteration=model.best_iteration)
    y = te["y"].values
    mae_m, mae_b = np.mean(np.abs(pred - y)), np.mean(np.abs(te["baseline"].values - y))
    imp = round(100 * (mae_b - mae_m) / mae_b, 2)
    name = f"nfl_{stat}_v1"
    model.save_model(str(MODEL_DIR / f"{name}.txt"))
    json.dump({"model_path": f"models/{name}.txt", "target": stat, "objective": obj,
               "feature_keys": FEATURE_KEYS, "best_iteration": model.best_iteration,
               "test_n": len(te), "mae_improvement_pct": imp, "trained_date": date.today().isoformat()},
              open(MODEL_DIR / f"{name}_meta.json", "w"), indent=2)
    log.info("nfl_trained", name=name, mae_model=round(mae_m, 2), mae_baseline=round(mae_b, 2),
             mae_improvement_pct=imp, n=len(te))
    return {"name": name, "improvement_pct": imp, "n": len(te)}


def main():
    configure_logging()
    out = load()
    results = [train_one(s, out) for s in SPECS]
    print("\n=== NFL models (MAE vs season-avg baseline) ===")
    for r in sorted(results, key=lambda x: -x["improvement_pct"]):
        verdict = "SHIP" if r["improvement_pct"] > 0 else "drop (worse than baseline)"
        print(f"  {r['name']:26} {r['improvement_pct']:+6.2f}%  (n={r['n']})  -> {verdict}")


if __name__ == "__main__":
    main()
