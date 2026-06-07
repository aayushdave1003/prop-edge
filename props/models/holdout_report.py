"""Out-of-sample performance & calibration report over settled picks.

Three views, all computed from the settled-pick history (the model's real
out-of-sample track record — these are predictions it made on games it never
trained on):

  1. Per sport×stat: settled n, win rate, gap to the 57.7% 2-pick breakeven.
  2. Calibration: bucket by predicted model_prob and compare predicted vs
     realized win rate — the honest test of whether "70%" really means 70%.
  3. Recent drift: last 21 days vs everything before, to catch a model that's
     quietly decayed.

Run:  python -m props.models.holdout_report
      python -m props.models.holdout_report --csv report.csv
"""
from __future__ import annotations

import argparse

import pandas as pd
from sqlalchemy import create_engine, text

BREAKEVEN = 0.577
CALIB_BUCKETS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.01]


def _engine():
    """Prefer the prod DB (canonical settled history); fall back to local."""
    from props.utils.config import settings
    url = settings.railway_database_url or settings.database_url
    return create_engine(url)


def load_settled(engine) -> pd.DataFrame:
    df = pd.read_sql(text("""
        SELECT g.sport_code AS sport, pk.stat_type, pk.direction,
               pk.model_prob, pk.leg_result,
               pk.picked_at::date AS pick_date
        FROM picks pk JOIN games g USING (game_id)
        WHERE pk.leg_result IN ('win', 'loss')
    """), engine)
    df["win"] = (df["leg_result"] == "win").astype(int)
    return df


def per_category(df: pd.DataFrame) -> pd.DataFrame:
    g = (df.groupby(["sport", "stat_type"])
           .agg(n=("win", "size"), win_rate=("win", "mean"),
                avg_prob=("model_prob", "mean"))
           .reset_index())
    g["vs_breakeven"] = g["win_rate"] - BREAKEVEN
    return g.sort_values("n", ascending=False)


def calibration(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bucket"] = pd.cut(df["model_prob"], bins=CALIB_BUCKETS, right=False)
    rows = []
    for b, grp in df.groupby("bucket", observed=True):
        if grp.empty:
            continue
        rows.append({
            "prob_bucket": f"{b.left:.0%}-{b.right:.0%}",
            "n": len(grp),
            "predicted": grp["model_prob"].mean(),
            "realized": grp["win"].mean(),
            "gap": grp["win"].mean() - grp["model_prob"].mean(),
        })
    return pd.DataFrame(rows)


def recent_drift(df: pd.DataFrame, days: int = 21) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    cutoff = df["pick_date"].max() - pd.Timedelta(days=days)
    df = df.copy()
    df["period"] = df["pick_date"].apply(lambda d: f"last {days}d" if d > cutoff else "earlier")
    return (df.groupby(["sport", "period"])
              .agg(n=("win", "size"), win_rate=("win", "mean"))
              .reset_index())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None, help="Write the calibration table to CSV")
    ap.add_argument("--days", type=int, default=21, help="Recent-drift window")
    args = ap.parse_args()

    df = load_settled(_engine())
    if df.empty:
        print("No settled picks yet.")
        return

    pd.set_option("display.width", 200)
    fmt = lambda x: f"{x:.3f}"  # noqa: E731

    print(f"\n=== Settled picks: {len(df)} ===")
    print(f"Overall win rate: {df['win'].mean():.1%}  (breakeven {BREAKEVEN:.1%})\n")

    cat = per_category(df)
    print("--- Per sport × stat ---")
    print(cat.to_string(index=False, formatters={
        "win_rate": fmt, "avg_prob": fmt, "vs_breakeven": lambda x: f"{x:+.3f}"}))

    cal = calibration(df)
    print("\n--- Calibration (predicted vs realized) ---")
    print(cal.to_string(index=False, formatters={
        "predicted": fmt, "realized": fmt, "gap": lambda x: f"{x:+.3f}"}))
    mae = (cal["gap"].abs() * cal["n"]).sum() / cal["n"].sum()
    print(f"weighted calibration MAE: {mae:.3f}  (lower = better; <0.05 is solid)")

    drift = recent_drift(df, args.days)
    if not drift.empty:
        print(f"\n--- Recent drift (last {args.days}d vs earlier) ---")
        print(drift.to_string(index=False, formatters={"win_rate": fmt}))

    if args.csv:
        cal.to_csv(args.csv, index=False)
        print(f"\nwrote calibration table -> {args.csv}")


if __name__ == "__main__":
    main()
