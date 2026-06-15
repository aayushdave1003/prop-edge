"""Compute back-to-back game indicator for NBA player-games.

Adds two features:
  - is_back_to_back (1 if team played yesterday, 0 otherwise)
  - team_days_rest (days since team's previous game)

Writes to player_games.derived JSONB (merges).
"""
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived


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

    items = [(int(row["player_game_id"]), {
        "team_days_rest": int(row["team_days_rest"]) if pd.notna(row["team_days_rest"]) else 7,
        "is_back_to_back": int(row["is_back_to_back"]) if pd.notna(row["is_back_to_back"]) else 0,
    }) for _, row in merged.iterrows()]
    write_derived(items, mode="merge", label="nba_back_to_back")

    log.info("nba_back_to_back_complete", rows=len(merged))


if __name__ == "__main__":
    run()
