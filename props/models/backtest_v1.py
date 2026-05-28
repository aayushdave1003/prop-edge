"""Version A backtest harness: model-accuracy diagnostics on the 2025+ holdout.

NOT an edge backtest. We don't have historical PrizePicks lines yet, so this
cannot tell you whether you'd beat PrizePicks. What it DOES tell you:

  - Where each model is accurate vs where it breaks down, sliced by:
      * line range (does it work at 1.5 but not 2.5?)
      * player volume (starters vs bench / high-PA vs low-PA)
      * month (calibration drift across the season)
  - Systematic over/under-prediction bias per model

Synthetic line = player's trailing season-avg rounded to nearest 0.5 (a fair-ish
stand-in for how books set lines). "Hit rate" = how often the model predicts the
correct side of that synthetic line vs the ACTUAL outcome. Use it as a RELATIVE
diagnostic across slices, never as an absolute edge number.

Usage: python -m props.models.backtest_v1
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy import stats as scipy_stats

from sqlalchemy import text
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.models.registry import MODELS


HOLDOUT_START = "2025-01-01"

# Map each stat_type to the season-avg feature used as the synthetic-line basis
SEASON_AVG_KEY = {
    "strikeouts_pitcher": "season_avg_strikeouts_pitcher",
    "hits": "season_avg_hits",
    "points": "season_avg_points",
    "rebounds": "season_avg_rebounds",
    "assists": "season_avg_assists",
}

# Which raw stat column in stats JSONB holds the actual outcome
ACTUAL_KEY = {
    "strikeouts_pitcher": "strikeouts_pitcher",
    "hits": "hits",
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
}

# Volume column to slice "starter vs bench" by, per sport
def volume_expr(sport, role):
    if sport == "nba":
        return "pg.minutes_played"
    if role == "pitcher":
        return "(pg.stats->>'batters_faced')::float"
    return "(pg.stats->>'plate_appearances')::float"


def round_half(x):
    return np.round(x * 2) / 2.0


def load_holdout(entry):
    meta = json.loads(Path(entry.meta_path).read_text())
    feature_keys = meta["feature_keys"]
    model = lgb.Booster(model_file=str(entry.model_path))

    vol = volume_expr(entry.sport_code, entry.role)
    # Role filter mirrors training
    if entry.sport_code == "nba":
        role_filter = "AND pg.minutes_played >= 10"
    elif entry.role == "pitcher":
        role_filter = "AND (pg.stats->>'batters_faced')::int >= 15"
    else:
        role_filter = "AND (pg.stats->>'plate_appearances')::int >= 3"

    sql = f"""
        SELECT pg.player_game_id, g.game_date, pg.derived, pg.stats,
               {vol} AS volume
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = :sport
          AND g.game_date >= :start
          {role_filter}
    """
    df = pd.read_sql(text(sql), engine, params={"sport": entry.sport_code, "start": HOLDOUT_START})
    if df.empty:
        return None, None, None, None

    df["game_date"] = pd.to_datetime(df["game_date"])
    derived = pd.json_normalize(df["derived"])
    stats = pd.json_normalize(df["stats"])

    X = pd.DataFrame()
    for k in feature_keys:
        X[k] = pd.to_numeric(derived[k], errors="coerce").fillna(0) if k in derived.columns else 0.0
    X = X.astype(float)

    actual = pd.to_numeric(stats[ACTUAL_KEY[entry.stat_type]], errors="coerce").fillna(0).values

    avg_key = SEASON_AVG_KEY[entry.stat_type]
    season_avg = pd.to_numeric(derived[avg_key], errors="coerce").fillna(0).values if avg_key in derived.columns else np.zeros(len(df))

    # filter rows with no history
    mask = season_avg > 0
    X = X[mask].reset_index(drop=True)
    actual = actual[mask]
    season_avg = season_avg[mask]
    meta_df = df[mask].reset_index(drop=True)

    pred = model.predict(X, num_iteration=model.best_iteration)
    return pred, actual, season_avg, meta_df


def diagnose(entry, pred, actual, season_avg, meta_df):
    line = round_half(season_avg)
    # avoid 0 lines
    line = np.where(line < 0.5, 0.5, line)

    # Model's predicted P(over line) via Poisson
    p_over = 1 - scipy_stats.poisson.cdf(line.astype(int), pred)
    model_side = np.where(p_over > 0.5, "over", "under")
    actual_over = actual > line
    actual_side = np.where(actual_over, "over", np.where(actual < line, "under", "push"))

    valid = actual_side != "push"
    correct = (model_side == actual_side) & valid

    overall_hit = correct[valid].mean() if valid.sum() else float("nan")
    bias = pred.mean() - actual.mean()   # + means model over-predicts

    print(f"\n{'='*60}")
    print(f"MODEL: {entry.name}  ({entry.sport_code} {entry.stat_type})")
    print(f"{'='*60}")
    print(f"Holdout rows: {len(pred)}   (non-push: {valid.sum()})")
    print(f"Overall side-accuracy vs synthetic line: {overall_hit:.1%}")
    print(f"Mean prediction: {pred.mean():.2f}   Mean actual: {actual.mean():.2f}   Bias: {bias:+.2f}")

    # Slice by line range
    print("\n-- by line value --")
    df = pd.DataFrame({"line": line, "correct": correct, "valid": valid})
    for lv, grp in df[df["valid"]].groupby("line"):
        if len(grp) >= 30:
            print(f"  line {lv:>4}: n={len(grp):>5}  acc={grp['correct'].mean():.1%}")

    # Slice by player volume (tertiles)
    print("\n-- by player volume (low / mid / high) --")
    vol = meta_df["volume"].fillna(0).values
    valid_vol = vol[valid]
    valid_correct = correct[valid]
    if len(valid_vol):
        q1, q2 = np.quantile(valid_vol, [0.33, 0.66])
        for label, lo, hi in [("low", -np.inf, q1), ("mid", q1, q2), ("high", q2, np.inf)]:
            m = (valid_vol > lo) & (valid_vol <= hi)
            if m.sum():
                print(f"  {label:>4} vol (<= {hi if hi!=np.inf else 'max':}): n={m.sum():>5}  acc={valid_correct[m].mean():.1%}")

    # Slice by month
    print("\n-- by month --")
    months = meta_df["game_date"].dt.to_period("M").astype(str).values[valid]
    mdf = pd.DataFrame({"month": months, "correct": correct[valid]})
    for mo, grp in mdf.groupby("month"):
        if len(grp) >= 30:
            print(f"  {mo}: n={len(grp):>5}  acc={grp['correct'].mean():.1%}")


def main():
    configure_logging()
    print("\nVERSION A BACKTEST -- model accuracy diagnostics, NOT an edge proof.")
    print("Synthetic line = season-avg rounded to .5. Use slices to find WHERE models break.\n")
    for entry in MODELS:
        try:
            pred, actual, season_avg, meta_df = load_holdout(entry)
            if pred is None:
                print(f"\n{entry.name}: no holdout rows, skipping")
                continue
            diagnose(entry, pred, actual, season_avg, meta_df)
        except Exception as e:
            print(f"\n{entry.name}: ERROR {e}")


if __name__ == "__main__":
    main()
