"""Compute absent-teammate usage features for NBA player-games.

For each player-game, identifies whether a key teammate was unexpectedly
absent and quantifies the usage that shifted to the remaining players.

Features added to player_games.derived:
  absent_teammate_avg_pts   — recent avg pts of the top absent teammate (0 if none)
  absent_teammate_avg_min   — recent avg min of the top absent teammate (0 if none)
  absent_teammate_avg_ast   — recent avg ast of the top absent teammate (0 if none)
  n_absent_teammates        — number of key teammates who didn't play this game
  expected_usage_bump       — absent_min / team_total_min_last10 (proportion of usage freed)

A teammate is "absent" if they averaged >= 15 min in their last 10 games but
played < 5 min (or not at all) in this specific game.
"""
import json
from datetime import datetime
import numpy as np
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


def load_nba_game_rosters() -> pd.DataFrame:
    """Load all NBA player-games with team context."""
    log.info("loading_nba_rosters_for_absence_features")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, pg.team_id,
               pg.minutes_played, pg.stats,
               g.game_date
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
          AND g.game_date >= NOW() - INTERVAL '365 days'
        ORDER BY pg.team_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", rows=len(df))
    return df


def _player_recent_stats(df: pd.DataFrame, player_id: int,
                          before_date, window: int = 10) -> dict:
    """Return avg pts/min/ast for a player over their last N games before a date."""
    player_games = df[
        (df["player_id"] == player_id) &
        (df["game_date"] < before_date) &
        (df["minutes_played"] >= 5)
    ].sort_values("game_date").tail(window)

    if player_games.empty:
        return {"avg_pts": 0.0, "avg_min": 0.0, "avg_ast": 0.0}

    stats = pd.json_normalize(player_games["stats"].tolist())
    return {
        "avg_pts": float(pd.to_numeric(stats.get("points", pd.Series([0])),
                                        errors="coerce").fillna(0).mean()),
        "avg_min": float(player_games["minutes_played"].mean()),
        "avg_ast": float(pd.to_numeric(stats.get("assists", pd.Series([0])),
                                        errors="coerce").fillna(0).mean()),
    }


def compute_absence_features(df: pd.DataFrame) -> pd.DataFrame:
    """For each player-game, compute absent-teammate features."""
    log.info("computing_absence_features")
    results = []

    # Group by game+team to identify who played and who didn't
    for (game_id, team_id), game_team in df.groupby(["game_id", "team_id"]):
        game_date = game_team["game_date"].iloc[0]

        # Players who actually played this game (>= 5 min)
        played = game_team[game_team["minutes_played"] >= 5]
        played_ids = set(played["player_id"].tolist())

        # All players on this team in the last 30 days before this game
        team_history = df[
            (df["team_id"] == team_id) &
            (df["game_date"] < game_date) &
            (df["game_date"] >= game_date - pd.Timedelta(days=30)) &
            (df["minutes_played"] >= 5)
        ]

        if team_history.empty:
            for _, row in game_team.iterrows():
                results.append({
                    "player_game_id": row["player_game_id"],
                    "absent_teammate_avg_pts": 0.0,
                    "absent_teammate_avg_min": 0.0,
                    "absent_teammate_avg_ast": 0.0,
                    "n_absent_teammates":      0,
                    "expected_usage_bump":     0.0,
                })
            continue

        # Recent avg minutes per player on this team
        recent_avg_min = (
            team_history.groupby("player_id")["minutes_played"]
            .mean()
            .reset_index()
            .rename(columns={"minutes_played": "recent_avg_min"})
        )
        recent_avg_min = recent_avg_min[recent_avg_min["recent_avg_min"] >= 15]

        # Who was absent? Regular rotation (>=15 avg min) but didn't play today
        absent_ids = set(recent_avg_min["player_id"].tolist()) - played_ids
        n_absent = len(absent_ids)

        if n_absent == 0:
            for _, row in game_team.iterrows():
                results.append({
                    "player_game_id":          row["player_game_id"],
                    "absent_teammate_avg_pts":  0.0,
                    "absent_teammate_avg_min":  0.0,
                    "absent_teammate_avg_ast":  0.0,
                    "n_absent_teammates":       0,
                    "expected_usage_bump":      0.0,
                })
            continue

        # Find the top absent teammate by recent avg minutes
        top_absent = (
            recent_avg_min[recent_avg_min["player_id"].isin(absent_ids)]
            .sort_values("recent_avg_min", ascending=False)
            .iloc[0]
        )
        top_absent_id = int(top_absent["player_id"])
        top_stats = _player_recent_stats(df, top_absent_id, game_date)

        # Total absent minutes as fraction of team's normal total
        absent_min_total = float(
            recent_avg_min[recent_avg_min["player_id"].isin(absent_ids)]["recent_avg_min"].sum()
        )
        team_total_min = float(recent_avg_min["recent_avg_min"].sum())
        usage_bump = round(absent_min_total / team_total_min, 4) if team_total_min > 0 else 0.0

        for _, row in game_team.iterrows():
            pid = int(row["player_id"])
            # Only flag healthy players (those who actually played)
            if pid in played_ids:
                results.append({
                    "player_game_id":          row["player_game_id"],
                    "absent_teammate_avg_pts":  round(top_stats["avg_pts"], 4),
                    "absent_teammate_avg_min":  round(top_stats["avg_min"], 4),
                    "absent_teammate_avg_ast":  round(top_stats["avg_ast"], 4),
                    "n_absent_teammates":       n_absent,
                    "expected_usage_bump":      usage_bump,
                })
            else:
                results.append({
                    "player_game_id":          row["player_game_id"],
                    "absent_teammate_avg_pts":  0.0,
                    "absent_teammate_avg_min":  0.0,
                    "absent_teammate_avg_ast":  0.0,
                    "n_absent_teammates":       0,
                    "expected_usage_bump":      0.0,
                })

    out = pd.DataFrame(results)
    flagged = (out["n_absent_teammates"] > 0).sum()
    log.info("absence_features_computed", rows=len(out), flagged=int(flagged))
    return out


def merge_into_derived(feature_df: pd.DataFrame, batch_size: int = 2000):
    """Merge absence features into existing player_games.derived JSONB."""
    log.info("merging_absence_features_into_derived", rows=len(feature_df))
    cols = [c for c in feature_df.columns if c != "player_game_id"]

    with session_scope() as session:
        for i in range(0, len(feature_df), batch_size):
            batch = feature_df.iloc[i:i + batch_size]
            for _, row in batch.iterrows():
                patch = {c: float(row[c]) if not isinstance(row[c], int)
                         else int(row[c]) for c in cols}
                session.execute(text("""
                    UPDATE player_games
                    SET derived = derived || CAST(:patch AS JSONB),
                        updated_at = NOW()
                    WHERE player_game_id = :pid
                """), {"patch": json.dumps(patch), "pid": int(row["player_game_id"])})
            if (i // batch_size) % 10 == 0:
                log.info("merge_progress",
                         done=min(i + batch_size, len(feature_df)), total=len(feature_df))


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('nba_teammate_absence', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()

    df = load_nba_game_rosters()
    feature_df = compute_absence_features(df)
    merge_into_derived(feature_df)

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(feature_df), "rid": run_id})
    log.info("nba_teammate_absence_complete", updated=len(feature_df))


if __name__ == "__main__":
    run()
