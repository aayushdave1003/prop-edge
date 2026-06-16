"""Risk-free A/B of the opponent-adjusted (SoS) batter features.

`mlb_batter_sos` builds `last_10_avg_faced_era` / `last_10_avg_faced_k_rate` but
the feature was never A/B'd: wiring it into the models needs the full prod derived
backfill (the disk-risk op), so the decision stalled. This tests it WITHOUT
touching prod or writing a single row — it recomputes the EXACT values the backfill
would write (same query + shift(1).rolling(10) as `mlb_batter_sos.run`), injects
them into the hits/total_bases training frames, retrains a candidate
(FEATURE_KEYS + SoS) against a baseline (FEATURE_KEYS) on the identical split, and
reports held-out MAE. Same gate as the real retrain loop: a feature only earns its
keep at >= MIN_PROMOTE_PCT (0.5%) MAE improvement.

SoS is a batter feature (quality of pitchers faced), so it only applies to the
batter models — not the pitcher strikeouts model.

Run:  DATABASE_URL=$RAILWAY_DATABASE_URL python -m props.features.sos_eval
"""
from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import configure_logging, log
from props.models.retrain_and_promote import MIN_PROMOTE_PCT

W = 10
SOS_KEYS = ["last_10_avg_faced_era", "last_10_avg_faced_k_rate"]
MODELS = ["props.models.hits_v1", "props.models.total_bases_v1"]


def compute_sos_map() -> dict[int, dict]:
    """The exact map `mlb_batter_sos.run()` would write — returned, not stored."""
    df = pd.read_sql(text("""
        SELECT pg.player_game_id, pg.player_id, g.game_date,
               (pg.derived->>'pitcher_last_10_era')::float    AS era,
               (pg.derived->>'pitcher_last_10_k_rate')::float AS krate
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'plate_appearances')::int > 0
          AND pg.derived ? 'pitcher_last_10_era'
    """), engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values(["player_id", "game_date", "player_game_id"]).reset_index(drop=True)
    feats: dict[int, dict] = {}
    for _, g in df.groupby("player_id", group_keys=False):
        g = g.sort_values(["game_date", "player_game_id"])
        era = g["era"].shift(1).rolling(W, min_periods=1).mean()
        kr = g["krate"].shift(1).rolling(W, min_periods=1).mean()
        for pgid, e, k in zip(g["player_game_id"], era, kr):
            feats[int(pgid)] = {
                "last_10_avg_faced_era": round(float(e), 4) if pd.notna(e) else 0.0,
                "last_10_avg_faced_k_rate": round(float(k), 4) if pd.notna(k) else 0.0,
            }
    return feats


def _mae(model, test_df, keys) -> float:
    pred = model.predict(test_df[keys], num_iteration=model.best_iteration)
    return float(np.mean(np.abs(pred - test_df["y"].to_numpy(dtype=float))))


def eval_model(mod_name: str, sos_map: dict[int, dict]) -> dict:
    M = importlib.import_module(mod_name)
    stat = M.TARGET
    df = M.load_training_data()
    pgid = df["player_game_id"].astype(int)
    for key in SOS_KEYS:
        df[key] = pgid.map(lambda i, k=key: sos_map.get(i, {}).get(k, 0.0)).astype(float)
    coverage = float((df[SOS_KEYS].abs().sum(axis=1) > 0).mean())

    train_df, test_df = M.split_train_test(df)
    val_cutoff = train_df["game_date"].max() - pd.Timedelta(days=30)
    fit = train_df[train_df["game_date"] < val_cutoff]
    val = train_df[train_df["game_date"] >= val_cutoff]

    base_keys = list(M.FEATURE_KEYS)
    cand_keys = base_keys + SOS_KEYS
    try:
        base = M.train_model(fit, val)
        mae_base = _mae(base, test_df, base_keys)
        M.FEATURE_KEYS = cand_keys                # train_model reads the global
        cand = M.train_model(fit, val)
        mae_cand = _mae(cand, test_df, cand_keys)
    finally:
        M.FEATURE_KEYS = base_keys

    impr = 100.0 * (mae_base - mae_cand) / mae_base
    # how the candidate ranks the SoS keys (gain), for context
    gains = dict(zip(cand.feature_name(), cand.feature_importance(importance_type="gain")))
    total_gain = sum(gains.values()) or 1.0
    sos_gain_pct = 100.0 * sum(gains.get(k, 0) for k in SOS_KEYS) / total_gain
    return {"stat": stat, "n_test": len(test_df), "coverage": coverage,
            "mae_base": mae_base, "mae_cand": mae_cand, "improvement_pct": impr,
            "sos_gain_pct": sos_gain_pct}


def main():
    configure_logging()
    from props.utils.db import db_banner
    print(db_banner())
    sos_map = compute_sos_map()
    print(f"SoS map: {len(sos_map):,} player-games\n")
    print(f"{'stat':<14}{'n_test':>8}{'cover':>8}{'MAE base':>10}{'MAE +SoS':>10}"
          f"{'Δ MAE %':>10}{'SoS gain%':>11}  verdict")
    results = []
    for m in MODELS:
        r = eval_model(m, sos_map)
        results.append(r)
        verdict = "PROMOTE" if r["improvement_pct"] >= MIN_PROMOTE_PCT else "keep baseline"
        print(f"{r['stat']:<14}{r['n_test']:>8,}{r['coverage']:>8.2f}"
              f"{r['mae_base']:>10.4f}{r['mae_cand']:>10.4f}"
              f"{r['improvement_pct']:>+10.2f}{r['sos_gain_pct']:>10.1f}%  {verdict}")
    win = any(r["improvement_pct"] >= MIN_PROMOTE_PCT for r in results)
    print(f"\nGate: candidate must beat baseline by >= {MIN_PROMOTE_PCT}% MAE.")
    print("VERDICT:", "SoS earns its keep on >=1 model — wire it in."
          if win else "SoS does not clear the bar — leave it built-but-unwired.")


if __name__ == "__main__":
    main()
