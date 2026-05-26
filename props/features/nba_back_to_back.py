"""Compute back-to-back game indicator for NBA player-games.

Adds two features:
  - is_back_to_back (1 if team played yesterday, 0 otherwise)
  - team_days_rest (days since team's previous game)

Writes to player_games.derived JSONB (merges).
"""
import json
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


def run():
    configure_logging()
    log.info("loading_nba_games_by_team")
    sql = """
        SELECT DISTINCT pg.team_id, pg.game_id, g.game_date
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
        ORDER BY pg.team_id, g.game_date, pg.game_id
    """
    team_games = pd.read_sql(sql, engine)
    team_games["game_date"] = pd.to_datetime(team_games["game_date"])

    # For each team, compute days since prior game
    team_games = team_games.sort_values(["team_id", "game_date", "game_id"])
    team_games["prior_date"] = team_games.groupby("team_id")["game_date"].shift(1)
    team_games["team_days_rest"] = (team_games["game_date"] - team_games["prior_date"]).dt.days.fillna(7).astype(int).clip(upper=14)
    team_games["is_back_to_back"] = (team_games["team_days_rest"] == 1).astype(int)

    # Now join to all player_games
    log.info("loading_player_games")
    pg_df = pd.read_sql(text("""
        SELECT pg.player_game_id, pg.team_id, pg.game_id, pg.derived
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
    """), engine)

    merged = pg_df.merge(
        team_games[["team_id", "game_id", "team_days_rest", "is_back_to_back"]],
        on=["team_id", "game_id"], how="left"
    )
    log.info("merged", rows=len(merged))

    with session_scope() as session:
        for i, row in merged.iterrows():
            existing = row["derived"] or {}
            if not isinstance(existing, dict):
                existing = json.loads(existing) if existing else {}
            existing["team_days_rest"] = int(row["team_days_rest"]) if pd.notna(row["team_days_rest"]) else 7
            existing["is_back_to_back"] = int(row["is_back_to_back"]) if pd.notna(row["is_back_to_back"]) else 0
            session.execute(text("""
                UPDATE player_games
                SET derived = CAST(:d AS JSONB), updated_at = NOW()
                WHERE player_game_id = :pid
            """), {"d": json.dumps(existing), "pid": int(row["player_game_id"])})
            if i % 5000 == 0 and i > 0:
                log.info("write_progress", done=i, total=len(merged))

    log.info("nba_back_to_back_complete", rows=len(merged))


if __name__ == "__main__":
    run()
