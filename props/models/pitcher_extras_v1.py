"""Train pitcher per-start markets: earned_runs_allowed + hits_allowed.

Unlike low-frequency batter events (runs/doubles/steals — all busts), a starter's
ER and hits-allowed accumulate over ~25 batters faced, so they carry real signal:
the pitcher's own recent form + command and the opposing lineup's hitting quality.
Both clear their season-avg baseline (ER +8%, hits_allowed +18%). Reuses the
strikeouts_v1 pitcher feature set. Filter: batters_faced >= 15 (true starts).

    python -m props.models.pitcher_extras_v1
"""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.models.strikeouts_v1 import FEATURE_KEYS

MODEL_DIR = Path("models"); MODEL_DIR.mkdir(exist_ok=True)
# stat_type (matches prop_lines) -> box-score key for the target / its baseline
SPECS = {
    "earned_runs_allowed": ("earned_runs", "season_avg_earned_runs"),
    "hits_allowed":        ("hits_allowed", "season_avg_hits_allowed"),
}


def load():
    log.info("loading_training_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code='mlb' AND (pg.stats->>'batters_faced')::int >= 15
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    derived = pd.json_normalize(df["derived"]); stats = pd.json_normalize(df["stats"])
    keys = list(dict.fromkeys(FEATURE_KEYS + [b for _, b in SPECS.values()]))
    out = pd.DataFrame({"player_game_id": df["player_game_id"].values, "game_date": df["game_date"].values})
    for k in keys:
        out[k] = pd.to_numeric(derived.get(k, 0), errors="coerce").fillna(0)
    for box, _ in SPECS.values():
        out[f"_st_{box}"] = pd.to_numeric(stats.get(box, 0), errors="coerce").fillna(0)
    out = out[out["last_10_avg_strikeouts_pitcher"] > 0].copy()
    log.info("filtered_starts", n=len(out))
    return out


def train_one(stat, box, baseline, out):
    d = out.copy(); d["y"] = d[f"_st_{box}"].astype(int)
    tr, te = d[d.game_date < "2025-01-01"], d[d.game_date >= "2025-01-01"]
    vc = tr.game_date.max() - pd.Timedelta(days=30)
    fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
    params = {"objective": "poisson", "metric": ["poisson", "mae"], "learning_rate": 0.04,
              "num_leaves": 31, "min_data_in_leaf": 50, "feature_fraction": 0.9,
              "bagging_fraction": 0.9, "bagging_freq": 5, "verbose": -1, "seed": 42}
    model = lgb.train(params, lgb.Dataset(fit[FEATURE_KEYS], fit["y"]), num_boost_round=2000,
                      valid_sets=[lgb.Dataset(val[FEATURE_KEYS], val["y"])],
                      callbacks=[lgb.early_stopping(50)])
    pred = model.predict(te[FEATURE_KEYS], num_iteration=model.best_iteration); y = te["y"].values
    mae_m, mae_b = np.mean(np.abs(pred - y)), np.mean(np.abs(te[baseline].values - y))
    imp = round(100 * (mae_b - mae_m) / mae_b, 2)
    name = f"{stat}_v1"
    model.save_model(str(MODEL_DIR / f"{name}.txt"))
    meta = {"model_path": f"models/{name}.txt", "target": stat, "feature_keys": FEATURE_KEYS,
            "best_iteration": model.best_iteration, "test_n": len(te),
            "mae_improvement_pct": imp, "trained_date": date.today().isoformat()}
    with open(MODEL_DIR / f"{name}_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    log.info("trained", stat=stat, mae_model=round(mae_m, 4), mae_baseline=round(mae_b, 4),
             mae_improvement_pct=imp, n=len(te))
    return {"stat": stat, "improvement_pct": imp, "n": len(te)}


def main():
    configure_logging()
    out = load()
    results = [train_one(stat, box, base, out) for stat, (box, base) in SPECS.items()]
    print("\n=== pitcher markets (MAE vs season-avg baseline) ===")
    for r in sorted(results, key=lambda x: -x["improvement_pct"]):
        verdict = "SHIP" if r["improvement_pct"] > 0 else "drop (worse than baseline)"
        print(f"  {r['stat']:20} {r['improvement_pct']:+6.2f}%  (n={r['n']})  -> {verdict}")


if __name__ == "__main__":
    main()
