"""Compute rolling features for NHL player-games.

Skaters: goals, assists, points, shots, hits, blocked_shots
Goalies: saves, goals_against, save_pct (identified by position='G')
"""
import json
from datetime import datetime
import pandas as pd
import numpy as np
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived, feat_dict

WINDOWS = [5, 10, 20]

SKATER_STATS = [
    "goals", "assists", "points", "shots", "hits", "blocked_shots",
    "powerplay_goals", "powerplay_points", "penalty_minutes", "minutes",
]

GOALIE_STATS = ["saves", "goals_against", "shots_against", "minutes"]

THRESHOLDS = {
    "points":        [0.5, 1.5, 2.5],
    "goals":         [0.5, 1.5],
    "assists":       [0.5, 1.5, 2.5],
    "shots":         [2.5, 3.5, 4.5],
    "hits":          [1.5, 2.5, 3.5],
    "blocked_shots": [0.5, 1.5, 2.5],
    "saves":         [19.5, 24.5, 29.5],
}


def load_player_games() -> pd.DataFrame:
    log.info("loading_nhl_player_games")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, g.game_date, g.season,
               pg.stats, pg.minutes_played, p.position
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p ON p.player_id = pg.player_id
        WHERE g.sport_code = 'nhl'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", n=len(df))
    return df


def explode_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = pd.json_normalize(df["stats"])
    out = df.drop(columns=["stats"]).reset_index(drop=True)
    all_stats = list(set(SKATER_STATS + GOALIE_STATS))
    for col in all_stats:
        out[col] = pd.to_numeric(stats.get(col, 0), errors="coerce").fillna(0)
    return out


def compute_rolling_for_player(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
    is_goalie = (g["position"].iloc[0] == "G") if "position" in g.columns else False
    stat_cols = GOALIE_STATS if is_goalie else SKATER_STATS

    feats = {"player_game_id": g["player_game_id"].values}
    prior_dates = g["game_date"].shift(1)
    feats["days_rest"] = (g["game_date"] - prior_dates).dt.days.fillna(-1).astype(int).values

    season_marker = (g["season"] != g["season"].shift(1)).cumsum()
    feats["games_played_season"] = g.groupby(season_marker).cumcount().values

    for stat in stat_cols:
        if stat not in g.columns:
            continue
        pv = g[stat].shift(1)
        for w in WINDOWS:
            feats[f"last_{w}_avg_{stat}"] = pv.rolling(w, min_periods=1).mean().fillna(0).round(4).values
        cum = g.groupby(season_marker)[stat].apply(
            lambda s: s.shift(1).expanding().mean()
        ).reset_index(level=0, drop=True).fillna(0)
        feats[f"season_avg_{stat}"] = cum.round(4).values

    for stat, thresholds in THRESHOLDS.items():
        if stat not in g.columns:
            continue
        pv = g[stat].shift(1)
        for thr in thresholds:
            rate = pv.rolling(10, min_periods=1).apply(lambda x: (x > thr).mean(), raw=True).fillna(0)
            feats[f"last_10_rate_over_{thr}_{stat}"] = rate.round(4).values

    return pd.DataFrame(feats)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    log.info("computing_nhl_rolling_features")
    results = [compute_rolling_for_player(grp) for _, grp in df.groupby("player_id", group_keys=False)]
    out = pd.concat(results, ignore_index=True)
    log.info("rolling_features_computed", rows=len(out), features=len(out.columns) - 1)
    return out


def write_to_derived(feature_df: pd.DataFrame):
    cols = [c for c in feature_df.columns if c != "player_game_id"]
    items = [(int(row["player_game_id"]), feat_dict(row, cols))
             for _, row in feature_df.iterrows()]
    write_derived(items, mode="replace", label="nhl_rolling")


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('nhl_rolling', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()

    df = load_player_games()
    if df.empty:
        log.info("no_nhl_games_yet")
        return
    df  = explode_stats(df)
    out = compute_all(df)
    write_to_derived(out)

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(), rows_inserted=:n, status='success'
            WHERE run_id=:rid
        """), {"n": len(out), "rid": run_id})
    log.info("nhl_rolling_complete", updated=len(out))


if __name__ == "__main__":
    run()
