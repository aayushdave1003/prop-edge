"""Train CFB player-prop models: passing/rushing/receiving yards + receptions.
Reuses nfl_models_v1's config + per-target training; only the data (sport_code
'cfb') and model names differ. Each market trained on its volume group; assessed
vs the season-avg baseline."""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.models.nfl_models_v1 import FEATURE_KEYS, RAW

MODEL_DIR = Path("models"); MODEL_DIR.mkdir(exist_ok=True)

# CFB-specific filters: college box scores have no `targets`, so receiving is
# gated on receptions instead.
SPECS = {
    "passing_yards":   ("regression_l1", "season_avg_pass_attempts", 5.0),
    "rushing_yards":   ("regression_l1", "season_avg_carries",       3.0),
    "receiving_yards": ("regression_l1", "season_avg_receptions",    1.0),
    "receptions":      ("poisson",       "season_avg_receptions",    1.0),
}


def load():
    df = pd.read_sql("""
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id) WHERE g.sport_code='cfb'
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
    df = out[out[filt] >= minv].copy()
    df["y"], df["baseline"] = df[f"_st_{stat}"], df[f"season_avg_{stat}"]
    cutoff = df["game_date"].quantile(0.8)
    tr, te = df[df.game_date < cutoff], df[df.game_date >= cutoff]
    vc = tr["game_date"].quantile(0.85)
    fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
    if len(fit) < 50 or len(val) < 10 or len(te) < 10:
        log.info("cfb_skip_sparse", stat=stat, fit=len(fit), val=len(val), te=len(te))
        return {"name": f"cfb_{stat}_v1", "improvement_pct": float("nan"), "n": len(te)}
    params = {"objective": obj, "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 30,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
              "lambda_l2": 1.0, "verbose": -1, "seed": 42}
    model = lgb.train(params, lgb.Dataset(fit[FEATURE_KEYS], fit["y"]), num_boost_round=600,
                      valid_sets=[lgb.Dataset(val[FEATURE_KEYS], val["y"])], callbacks=[lgb.early_stopping(40)])
    pred = model.predict(te[FEATURE_KEYS], num_iteration=model.best_iteration); y = te["y"].values
    mae_m, mae_b = np.mean(np.abs(pred - y)), np.mean(np.abs(te["baseline"].values - y))
    imp = round(100 * (mae_b - mae_m) / mae_b, 2)
    name = f"cfb_{stat}_v1"
    model.save_model(str(MODEL_DIR / f"{name}.txt"))
    json.dump({"model_path": f"models/{name}.txt", "target": stat, "objective": obj,
               "feature_keys": FEATURE_KEYS, "best_iteration": model.best_iteration,
               "test_n": len(te), "mae_improvement_pct": imp, "trained_date": date.today().isoformat()},
              open(MODEL_DIR / f"{name}_meta.json", "w"), indent=2)
    log.info("cfb_trained", name=name, mae_improvement_pct=imp, n=len(te))
    return {"name": name, "improvement_pct": imp, "n": len(te)}


def main():
    configure_logging()
    out = load()
    results = [train_one(s, out) for s in SPECS]
    print("\n=== CFB models (MAE vs season-avg baseline) ===")
    for r in sorted(results, key=lambda x: x["improvement_pct"] if x["improvement_pct"] == x["improvement_pct"] else -1e9, reverse=True):
        imp = r["improvement_pct"]
        if imp != imp:
            print(f"  {r['name']:26}  (n={r['n']})  -> skipped (sparse)")
        else:
            print(f"  {r['name']:26} {imp:+6.2f}%  (n={r['n']})  -> {'SHIP' if imp > 0 else 'drop (worse than baseline)'}")


if __name__ == "__main__":
    main()
