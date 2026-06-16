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


# Each model trains ONLY on batters with rolling history and a real appearance
# (see each model's load_training_data). Scoring the A/B on rows OUTSIDE that
# domain — cold-start batters with no last-10 history, which the model never
# trains on and only extrapolates to — measures extrapolation noise, not model
# quality. It mis-rejected the SoS hits model at -1.34% on the full population
# when the in-domain truth was +0.83%. So the gate must score the SAME population
# the model serves: last_10_avg_at_bats>0 plus the model's appearance threshold.
_APPEARANCE = {
    "hits":        "(pg.stats->>'plate_appearances')::int >= 3",
    "total_bases": "(pg.stats->>'plate_appearances')::int >= 3",
    "home_runs":   "(pg.stats->>'at_bats')::numeric > 0",
}


def _load_recent(stat: str, days: int) -> pd.DataFrame:
    appearance = _APPEARANCE.get(stat, "(pg.stats->>'plate_appearances')::int >= 1")
    return pd.read_sql(text(f"""
        SELECT pg.derived, (pg.stats->>:stat)::float AS y
        FROM player_games pg
        JOIN games g ON g.game_id = pg.game_id
        WHERE g.sport_code = 'mlb' AND g.status = 'final'
          AND g.game_date >= (CURRENT_DATE - make_interval(days => :d))
          AND (pg.stats->>:stat) IS NOT NULL
          AND pg.derived IS NOT NULL
          AND (pg.derived->>'last_10_avg_at_bats')::float > 0
          AND {appearance}
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


def compare(stat: str, candidate: str, days: int = 45) -> dict | None:
    """Score a candidate model vs prod on recent settled games (read-only).

    Returns a dict {stat, n, mae_prod, mae_cand, improvement_pct, winner} or
    None when there's no settled data. Shared by the CLI (`run`) and the
    auto-retrain pipeline so the promote gate and the manual check use identical
    math.
    """
    if stat not in STAT_MODEL:
        raise ValueError(f"unknown stat {stat}; choose from {list(STAT_MODEL)}")
    prod_path = Path("models") / f"{STAT_MODEL[stat]}.txt"
    df = _load_recent(stat, days)
    if df.empty:
        return None
    y = df["y"].values
    mae_prod = float(np.mean(np.abs(_predict(prod_path, df) - y)))
    mae_cand = float(np.mean(np.abs(_predict(Path(candidate), df) - y)))
    improvement = 100 * (mae_prod - mae_cand) / mae_prod if mae_prod else 0.0
    return {"stat": stat, "n": len(df), "mae_prod": mae_prod, "mae_cand": mae_cand,
            "improvement_pct": improvement,
            "winner": "candidate" if mae_cand < mae_prod else "prod"}


def run(stat: str, candidate: str, days: int = 45, do_log: bool = False):
    configure_logging()
    if stat not in STAT_MODEL:
        raise SystemExit(f"unknown stat {stat}; choose from {list(STAT_MODEL)}")
    res = compare(stat, candidate, days)
    if res is None:
        log.info("ab_no_data", stat=stat)
        print("No recent settled data for", stat)
        return
    mae_prod, mae_cand = res["mae_prod"], res["mae_cand"]
    improvement, winner = res["improvement_pct"], res["winner"]
    log.info("ab_compare", stat=stat, n=res["n"], mae_prod=round(mae_prod, 4),
             mae_candidate=round(mae_cand, 4), winner=winner,
             improvement_pct=round(improvement, 2))
    print(f"\nA/B {stat} (n={res['n']}, last {days}d):")
    print(f"  prod      MAE {mae_prod:.4f}  ({STAT_MODEL[stat]}.txt)")
    print(f"  candidate MAE {mae_cand:.4f}  ({Path(candidate).name})")
    print(f"  → {winner.upper()} wins ({improvement:+.1f}% MAE vs prod)")

    if do_log:
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO backtest_runs
                        (run_at, sport, n_picks, mae_improvement_pct, trigger)
                    VALUES (NOW(), 'mlb', :n, :imp, :trig)
                """), {"n": res["n"], "imp": round(improvement, 2),
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
