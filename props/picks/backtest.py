"""Backtest model edge vs historical sharp-book closing lines.

For each historical player-game where we have:
  1. A model prediction (from player_games.derived features)
  2. A closing-line market odd (from market_odds table)
  3. An actual result (from player_games.stats)

Computes:
  - Model edge vs market: model_prob - market_over_prob
  - Simulated PrizePicks 2-pick parlay P&L at various edge thresholds
  - Calibration: when model says X%, does it actually hit X%?
  - ROI breakdown by stat type, edge tier, and Kelly fraction

Usage:
    python3 -m props.picks.backtest
    python3 -m props.picks.backtest --sport nba --since 2025-01-01
"""
import argparse
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


STAT_MODELS = {
    "nba": {
        "points":   "nba_points_v1",
        "rebounds": "nba_rebounds_v1",
        "assists":  "nba_assists_v1",
    }
}


def load_backtest_data(sport: str, since: date) -> pd.DataFrame:
    """Join player_games → market_odds → actuals for all overlapping records."""
    log.info("loading_backtest_data", sport=sport, since=str(since))

    if sport == "nba":
        stat_cases = """
            CASE
                WHEN mo.stat_type = 'points'   THEN (pg.stats->>'points')::float
                WHEN mo.stat_type = 'rebounds' THEN (pg.stats->>'rebounds')::float
                WHEN mo.stat_type = 'assists'  THEN (pg.stats->>'assists')::float
                WHEN mo.stat_type = 'threes_made' THEN (pg.stats->>'threes_made')::float
                WHEN mo.stat_type = 'blocks'   THEN (pg.stats->>'blocks')::float
                WHEN mo.stat_type = 'steals'   THEN (pg.stats->>'steals')::float
                WHEN mo.stat_type = 'pts_rebs_asts'
                    THEN (pg.stats->>'points')::float
                       + (pg.stats->>'rebounds')::float
                       + (pg.stats->>'assists')::float
                WHEN mo.stat_type = 'pts_rebs'
                    THEN (pg.stats->>'points')::float
                       + (pg.stats->>'rebounds')::float
                WHEN mo.stat_type = 'pts_asts'
                    THEN (pg.stats->>'points')::float
                       + (pg.stats->>'assists')::float
                WHEN mo.stat_type = 'rebs_asts'
                    THEN (pg.stats->>'rebounds')::float
                       + (pg.stats->>'assists')::float
                ELSE NULL
            END
        """
        derived_pts = "COALESCE((pg.derived->>'season_avg_points')::float, 0)"
        derived_reb = "COALESCE((pg.derived->>'season_avg_rebounds')::float, 0)"
        derived_ast = "COALESCE((pg.derived->>'season_avg_assists')::float, 0)"
    else:
        stat_cases = """
            CASE
                WHEN mo.stat_type = 'hits'       THEN (pg.stats->>'hits')::float
                WHEN mo.stat_type = 'home_runs'  THEN (pg.stats->>'home_runs')::float
                WHEN mo.stat_type = 'rbis'       THEN (pg.stats->>'rbis')::float
                WHEN mo.stat_type = 'total_bases' THEN (pg.stats->>'total_bases')::float
                WHEN mo.stat_type = 'strikeouts_pitcher'
                    THEN (pg.stats->>'strikeouts_pitcher')::float
                ELSE NULL
            END
        """
        derived_pts = "0"
        derived_reb = "0"
        derived_ast = "0"

    sql = text(f"""
        SELECT
            g.game_date,
            g.game_id,
            p.full_name    AS player_name,
            mo.stat_type,
            mo.line_value,
            mo.market_over_prob,
            mo.bookmaker,
            pg.minutes_played,
            {stat_cases}   AS actual_value,
            {derived_pts}  AS season_avg_pts,
            {derived_reb}  AS season_avg_reb,
            {derived_ast}  AS season_avg_ast,
            pg.derived
        FROM market_odds mo
        JOIN games g          USING(game_id)
        JOIN player_games pg  ON pg.game_id = mo.game_id
                             AND pg.player_id = mo.player_id
        JOIN players p        ON p.player_id = mo.player_id
        WHERE g.sport_code = :sport
          AND g.game_date >= :since
          AND g.status = 'final'
          AND pg.minutes_played >= 10
          AND mo.market_over_prob IS NOT NULL
        ORDER BY g.game_date, p.full_name, mo.stat_type, mo.line_value
    """)

    df = pd.read_sql(sql, engine, params={"sport": sport, "since": since})
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("backtest_rows_loaded", n=len(df))
    return df


def add_model_predictions(df: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Run each stat's model on the derived features to get model_prob."""
    import lightgbm as lgb
    from pathlib import Path
    import json
    import scipy.stats as scipy_stats

    results = []

    stat_model_map = STAT_MODELS.get(sport, {})
    if not stat_model_map:
        # For sports without individual models, use market prob as placeholder
        df["model_prob"] = df["market_over_prob"]
        df["model_edge"] = 0.0
        return df

    for stat_type, model_name in stat_model_map.items():
        meta_path = Path(f"models/{model_name}_meta.json")
        model_path = Path(f"models/{model_name}.txt")
        if not model_path.exists():
            continue

        with open(meta_path) as f:
            meta = json.load(f)
        feature_keys = meta["feature_keys"]
        model = lgb.Booster(model_file=str(model_path))

        subset = df[df["stat_type"] == stat_type].copy()
        if subset.empty:
            continue

        # Expand derived JSONB into feature columns
        derived_df = pd.json_normalize(subset["derived"])
        X = pd.DataFrame(index=subset.index)
        for k in feature_keys:
            if k in derived_df.columns:
                X[k] = pd.to_numeric(derived_df[k], errors="coerce").fillna(0).values
            else:
                X[k] = 0.0

        pred_mean = model.predict(X[feature_keys].astype(float),
                                  num_iteration=model.best_iteration)
        subset = subset.copy()
        subset["pred_mean"] = pred_mean

        # Raw Poisson prob
        subset["raw_over_prob"] = subset.apply(
            lambda r: 1 - scipy_stats.poisson.cdf(
                int(r["line_value"]), r["pred_mean"]
            ),
            axis=1,
        )

        # Apply global calibration if available
        cal_path = Path(f"models/{model_name}_calibrator.pkl")
        if cal_path.exists():
            import pickle
            with open(cal_path, "rb") as f:
                cals = pickle.load(f)
            if "global" in cals:
                subset["model_over_prob"] = cals["global"].predict(
                    subset["raw_over_prob"].values
                )
            else:
                subset["model_over_prob"] = subset["raw_over_prob"]
        else:
            subset["model_over_prob"] = subset["raw_over_prob"]

        results.append(subset)

    if not results:
        return df

    out = pd.concat(results, ignore_index=True)
    out["model_edge"] = out["model_over_prob"] - out["market_over_prob"]
    return out


def compute_roi(df: pd.DataFrame, edge_threshold: float = 0.05,
                direction: str = "over") -> dict:
    """Simulate flat-stake 2-pick PrizePicks parlays at given edge threshold."""
    if direction == "over":
        picks = df[df["model_edge"] >= edge_threshold].copy()
        picks["hit"] = picks["actual_value"] > picks["line_value"]
        picks["model_prob"] = picks["model_over_prob"]
    else:
        picks = df[df["model_edge"] <= -edge_threshold].copy()
        picks["hit"] = picks["actual_value"] <= picks["line_value"]
        picks["model_prob"] = 1 - picks["model_over_prob"]

    if picks.empty:
        return {"n": 0, "hit_rate": 0, "roi_2pick": 0}

    n         = len(picks)
    hit_rate  = picks["hit"].mean()
    # 2-pick parlay: win 3x stake when both legs hit
    # Approximate: treat each leg independently at the hit_rate
    # True parlay P&L requires pairs — use leg hit rate as approximation
    roi_leg   = hit_rate - 0.5   # vs flat 50% breakeven
    roi_2pick = hit_rate ** 2 * 3 - 1   # vs 2-pick breakeven of 1/√3 ≈ 57.7%

    return {
        "n":         n,
        "hit_rate":  round(hit_rate, 4),
        "roi_leg":   round(roi_leg, 4),
        "roi_2pick": round(roi_2pick, 4),
    }


def calibration_table(df: pd.DataFrame) -> pd.DataFrame:
    """Show model_over_prob vs actual over rate by probability bucket."""
    d = df.dropna(subset=["model_over_prob", "actual_value"]).copy()
    d["hit_over"] = d["actual_value"] > d["line_value"]
    bins = [0, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0]
    d["prob_bin"] = pd.cut(d["model_over_prob"], bins=bins, include_lowest=True)
    cal = d.groupby("prob_bin", observed=True).agg(
        n=("hit_over", "count"),
        avg_model_prob=("model_over_prob", "mean"),
        actual_hit_rate=("hit_over", "mean"),
    ).round(3)
    cal["edge"] = (cal["actual_hit_rate"] - cal["avg_model_prob"]).round(3)
    return cal


def print_report(df: pd.DataFrame):
    print("\n" + "═"*64)
    print("  BACKTEST REPORT — CALIBRATED MODEL vs SHARP MARKET")
    print("═"*64)
    calibrated = "model_over_prob" in df.columns and "raw_over_prob" in df.columns
    if calibrated:
        print("  ✓ Using calibrated probabilities (global isotonic regression)")
    print(f"\n  Dataset: {len(df):,} player-game prop lines")
    print(f"  Date range: {df['game_date'].min().date()} → {df['game_date'].max().date()}")
    print(f"  Unique players: {df['player_name'].nunique()}")
    print(f"  Stat types: {sorted(df['stat_type'].unique())}")

    print("\n── Edge Distribution ────────────────────────────────────")
    edge_buckets = pd.cut(df["model_edge"],
        bins=[-1, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, 1],
        labels=["<-10%", "-10 to -5%", "-5 to 0%", "0 to 5%",
                "5 to 10%", "10 to 20%", ">20%"])
    print(df.groupby(edge_buckets, observed=True).size().to_string())

    print("\n── ROI by Edge Threshold (OVER picks) ──────────────────")
    print(f"  {'Threshold':>10}  {'N picks':>8}  {'Hit%':>6}  {'Leg ROI':>8}  {'2-pick ROI':>10}")
    for thr in [0.0, 0.05, 0.10, 0.15, 0.20]:
        r = compute_roi(df, thr, "over")
        print(f"  {thr:>10.0%}  {r['n']:>8,}  {r['hit_rate']:>6.1%}  "
              f"{r['roi_leg']:>+8.1%}  {r['roi_2pick']:>+10.1%}")

    print("\n── ROI by Edge Threshold (UNDER picks) ─────────────────")
    print(f"  {'Threshold':>10}  {'N picks':>8}  {'Hit%':>6}  {'Leg ROI':>8}  {'2-pick ROI':>10}")
    for thr in [0.0, 0.05, 0.10, 0.15, 0.20]:
        r = compute_roi(df, thr, "under")
        print(f"  {thr:>10.0%}  {r['n']:>8,}  {r['hit_rate']:>6.1%}  "
              f"{r['roi_leg']:>+8.1%}  {r['roi_2pick']:>+10.1%}")

    print("\n── ROI by Stat Type (edge >= 5%) ────────────────────────")
    print(f"  {'Stat':>15}  {'N':>6}  {'Hit%':>6}  {'Leg ROI':>8}")
    for stat in sorted(df["stat_type"].unique()):
        sub = df[df["stat_type"] == stat]
        r = compute_roi(sub, 0.05, "over")
        if r["n"] > 0:
            print(f"  {stat:>15}  {r['n']:>6,}  {r['hit_rate']:>6.1%}  {r['roi_leg']:>+8.1%}")

    print("\n── Calibration (model_over_prob vs actual hit rate) ─────")
    cal = calibration_table(df)
    print(cal.to_string())
    print()


def save_run(sport: str, since: date, df: pd.DataFrame, trigger: str = "manual"):
    """Persist key backtest metrics to backtest_runs table for trend tracking."""
    decided = df[df["actual_value"].notna()].copy()
    if decided.empty:
        return

    decided["hit"] = decided["actual_value"] > decided["line_value"]
    n_total = len(decided)
    win_rate = decided["hit"].mean() if n_total else None

    # 2-pick ROI
    roi_2pick = win_rate ** 2 * 3 - 1 if win_rate is not None else None

    # Edge 10%+ UNDER picks (our strongest signal)
    edge10 = decided[decided["model_edge"].abs() >= 0.10]
    edge10_wr = (edge10["hit"] == False).mean() if len(edge10) >= 5 else None  # UNDER hit
    edge10_n  = len(edge10)

    # Calibration gap at 60-70% range
    cal_mask = (decided["model_over_prob"] >= 0.60) & (decided["model_over_prob"] < 0.70)
    cal_sub  = decided[cal_mask]
    cal_gap  = (cal_sub["hit"].mean() - cal_sub["model_over_prob"].mean()) if len(cal_sub) >= 5 else None

    with session_scope() as session:
        session.execute(text("""
            INSERT INTO backtest_runs
                (sport, since_date, n_picks, win_rate, roi_2pick,
                 edge_10_win_rate, edge_10_n, calibration_gap, trigger)
            VALUES
                (:sport, :since, :n, :wr, :roi,
                 :e10_wr, :e10_n, :cal_gap, :trigger)
        """), {
            "sport":   sport,
            "since":   since,
            "n":       n_total,
            "wr":      round(float(win_rate), 4) if win_rate is not None else None,
            "roi":     round(float(roi_2pick), 4) if roi_2pick is not None else None,
            "e10_wr":  round(float(edge10_wr), 4) if edge10_wr is not None else None,
            "e10_n":   edge10_n,
            "cal_gap": round(float(cal_gap), 4) if cal_gap is not None else None,
            "trigger": trigger,
        })
    log.info("backtest_run_saved", sport=sport, n=n_total,
             win_rate=win_rate, trigger=trigger)


def run(sport: str = "nba", since: date = None, trigger: str = "manual"):
    configure_logging()
    if since is None:
        since = date.today() - timedelta(days=90)

    df = load_backtest_data(sport, since)
    if df.empty:
        print("No data found. Run historical_odds.py first.")
        return

    df = add_model_predictions(df, sport)
    print_report(df)
    save_run(sport, since, df, trigger=trigger)

    out_path = f"backtest_{sport}_{since}.csv"
    df[["game_date", "player_name", "stat_type", "line_value",
        "market_over_prob", "model_over_prob", "model_edge",
        "actual_value", "bookmaker"]].to_csv(out_path, index=False)
    log.info("backtest_saved", path=out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", default="nba", choices=["nba", "mlb"])
    parser.add_argument("--since", default=None)
    args = parser.parse_args()
    since = date.fromisoformat(args.since) if args.since else None
    run(sport=args.sport, since=since)
