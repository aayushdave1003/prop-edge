"""Compute minutes variance + win/loss streak features for NBA player-games.

Adds:
  - last_10_minutes_stddev (consistency of playing time)
  - team_last_5_wins (team's recent W-L streak)
  - team_won_last_game (1 if team won most recent game)

Writes to player_games.derived JSONB (merges).
"""
import json
import pandas as pd
import numpy as np
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


def run():
    configure_logging()

    # Player-level: minutes stddev over last 10
    log.info("loading_nba_player_games")
    df = pd.read_sql(text("""
        SELECT pg.player_game_id, pg.player_id, pg.team_id, pg.minutes_played,
               g.game_date, g.home_team_id, g.away_team_id,
               g.home_score, g.away_score, pg.derived
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """), engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", n=len(df))

    # Minutes stddev: rolling 10 of prior games (shift(1))
    df["min_stddev_last_10"] = df.groupby("player_id")["minutes_played"].apply(
        lambda s: s.shift(1).rolling(10, min_periods=2).std().fillna(0)
    ).reset_index(level=0, drop=True)

    # Team win/loss
    df["team_won"] = np.where(
        (df["team_id"] == df["home_team_id"]) & (df["home_score"] > df["away_score"]),
        1,
        np.where(
            (df["team_id"] == df["away_team_id"]) & (df["away_score"] > df["home_score"]),
            1, 0
        )
    )

    # Team last-5 wins (rolling, prior games only)
    df = df.sort_values(["team_id", "game_date", "player_game_id"])
    df["team_last_5_wins"] = df.groupby("team_id")["team_won"].apply(
        lambda s: s.shift(1).rolling(5, min_periods=1).sum().fillna(0)
    ).reset_index(level=0, drop=True)
    df["team_won_last_game"] = df.groupby("team_id")["team_won"].shift(1).fillna(0).astype(int)

    # Write back to derived
    log.info("writing_features", rows=len(df))
    with session_scope() as session:
        for i, row in df.iterrows():
            existing = row["derived"] or {}
            if not isinstance(existing, dict):
                existing = json.loads(existing) if existing else {}
            existing["min_stddev_last_10"] = float(row["min_stddev_last_10"]) if pd.notna(row["min_stddev_last_10"]) else 0.0
            existing["team_last_5_wins"] = int(row["team_last_5_wins"]) if pd.notna(row["team_last_5_wins"]) else 0
            existing["team_won_last_game"] = int(row["team_won_last_game"])
            session.execute(text("""
                UPDATE player_games SET derived = CAST(:d AS JSONB), updated_at = NOW()
                WHERE player_game_id = :pid
            """), {"d": json.dumps(existing), "pid": int(row["player_game_id"])})
            if i % 5000 == 0 and i > 0:
                log.info("progress", done=i, total=len(df))

    log.info("nba_streak_features_complete", rows=len(df))


if __name__ == "__main__":
    run()
