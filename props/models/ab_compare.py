"""A/B model comparison — score a CANDIDATE model against the live (prod) model
on recent settled games, completely risk-free (read-only; never touches picks).

For a stat, it loads recent MLB player-games with their derived features + the
actual outcome, runs BOTH the prod model (`models/<name>.txt`) and a candidate
model file on the same rows, and reports MAE for each — so you can decide whether
a retrain (e.g. with the new weather features) actually beats prod before
promoting it. With --log it records the result to `backtest_runs`
(trigger `ab:<stat>`), so the comparison shows on the Performance tab.

Run:  python -m props.models.ab_compare --stat total_bases \
          --candidate models/total_bases_v2.txt --log
"""
import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import log, configure_logging

STAT_MODEL = {
    "total_bases": "total_bases_v1",
    "hits": "hits_v1",
    "home_runs": "mlb_home_runs_v1",
}


def _load_recent(stat: str, days: int) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT pg.derived, (pg.stats->>:stat)::float AS y
        FROM player_games pg
        JOIN games g ON g.game_id = pg.game_id
        WHERE g.sport_code = 'mlb' AND g.status = 'final'
          AND g.game_date >= (CURRENT_DATE - make_interval(days => :d))
          AND (pg.stats->>:stat) IS NOT NULL
          AND pg.derived IS NOT NULL
    """), engine, params={"stat": stat, "d": days})


def _predict(model_path: Path, df: pd.DataFrame) -> np.ndarray:
    meta = json.loads(Path(str(model_path).replace(".txt", "_meta.json")).read_text())
    keys = meta["feature_keys"]
    booster = lgb.Booster(model_file=str(model_path))
    derived = pd.json_normalize(df["derived"])
    X = pd.DataFrame({
        k: pd.to_numeric(derived.get(k, pd.Series(0, index=df.index)),
                         errors="coerce").fillna(0).values
        for k in keys
    })
    return booster.predict(X)


def run(stat: str, candidate: str, days: int = 45, do_log: bool = False):
    configure_logging()
    if stat not in STAT_MODEL:
        raise SystemExit(f"unknown stat {stat}; choose from {list(STAT_MODEL)}")
    prod_path = Path("models") / f"{STAT_MODEL[stat]}.txt"
    df = _load_recent(stat, days)
    if df.empty:
        log.info("ab_no_data", stat=stat)
        print("No recent settled data for", stat)
        return
    y = df["y"].values
    mae_prod = float(np.mean(np.abs(_predict(prod_path, df) - y)))
    mae_cand = float(np.mean(np.abs(_predict(Path(candidate), df) - y)))
    improvement = 100 * (mae_prod - mae_cand) / mae_prod if mae_prod else 0.0
    winner = "candidate" if mae_cand < mae_prod else "prod"
    log.info("ab_compare", stat=stat, n=len(df), mae_prod=round(mae_prod, 4),
             mae_candidate=round(mae_cand, 4), winner=winner,
             improvement_pct=round(improvement, 2))
    print(f"\nA/B {stat} (n={len(df)}, last {days}d):")
    print(f"  prod      MAE {mae_prod:.4f}  ({prod_path.name})")
    print(f"  candidate MAE {mae_cand:.4f}  ({Path(candidate).name})")
    print(f"  → {winner.upper()} wins ({improvement:+.1f}% MAE vs prod)")

    if do_log:
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO backtest_runs
                        (run_at, sport, n_picks, mae_improvement_pct, trigger)
                    VALUES (NOW(), 'mlb', :n, :imp, :trig)
                """), {"n": len(df), "imp": round(improvement, 2),
                       "trig": f"ab:{stat}"})
            log.info("ab_logged", stat=stat)
        except Exception as e:
            log.warning("ab_log_failed", error=str(e)[:120])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stat", required=True, choices=list(STAT_MODEL))
    p.add_argument("--candidate", required=True, help="path to the candidate model .txt")
    p.add_argument("--days", type=int, default=45)
    p.add_argument("--log", action="store_true", help="record the result to backtest_runs")
    args = p.parse_args()
    run(args.stat, args.candidate, args.days, args.log)


if __name__ == "__main__":
    main()
