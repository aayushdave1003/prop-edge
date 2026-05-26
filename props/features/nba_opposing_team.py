"""Compute opposing-team rolling features for NBA player-games.

For each NBA player-game, attaches the opposing team's prior-10-game averages:
  - Points allowed per game (opp_drtg_proxy)
  - Pace (combined FGA + 0.4 * FTA + TOV - OREB, per Dean Oliver formula)
  - 3-point makes allowed
  - Rebounds allowed

Lookahead-safe: uses opposing team's prior games only.
Writes to player_games.derived JSONB (merges with existing keys).
"""
import json
from datetime import datetime
import pandas as pd
import numpy as np
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


WINDOWS = [5, 10]


def load_team_game_stats() -> pd.DataFrame:
    """For each (team, game), aggregate the box totals = what they ALLOWED."""
    log.info("loading_team_aggregates")
    sql = """
        SELECT pg.game_id, g.game_date, g.season,
               pg.team_id, pg.opponent_id,
               SUM((pg.stats->>'points')::int) AS pts_scored,
               SUM((pg.stats->>'rebounds')::int) AS reb_scored,
               SUM((pg.stats->>'threes_made')::int) AS threes_scored,
               SUM((pg.stats->>'fg_attempted')::int) AS fga,
               SUM((pg.stats->>'ft_attempted')::int) AS fta,
               SUM((pg.stats->>'turnovers')::int) AS tov,
               SUM((pg.stats->>'off_rebounds')::int) AS oreb
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
        GROUP BY pg.game_id, g.game_date, g.season, pg.team_id, pg.opponent_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])

    # Pace estimate (Oliver formula): FGA + 0.4*FTA + TOV - OREB
    df["possessions"] = df["fga"] + 0.4 * df["fta"] + df["tov"] - df["oreb"]
    log.info("team_game_rows", n=len(df))
    return df


def compute_opposing_features(team_df: pd.DataFrame) -> pd.DataFrame:
    """For each (team, game), compute their OPPONENT's rolling avg of what they allow.

    Process: sort by date, group by opponent_id, take rolling mean of stats
    they conceded, shift(1) to keep only prior-game data.
    """
    log.info("computing_opposing_team_features")

    # We want: for each game played by team T against opponent O,
    # what has O allowed in their last N games to OTHER teams?
    # We compute team_T's history first as "what they scored against various opponents"
    # Then for each game, the opponent's allowed stats = sum of stats other teams
    # scored against them in prior games.

    # team_df rows are (team_id, game_id) records of what team_id scored.
    # For "what was allowed against opponent_id", we look at all rows where
    # opponent_id == X, sorted by date, shifted, rolling mean.

    rows = []
    for opp_id, group in team_df.groupby("opponent_id", group_keys=False):
        g = group.sort_values(["game_date", "game_id"]).reset_index(drop=True)
        feats = {
            "team_id": g["opponent_id"].values,  # this is the perspective: what THIS team allows
            "game_id": g["game_id"].values,
        }
        for stat in ["pts_scored", "reb_scored", "threes_scored", "possessions"]:
            prior = g[stat].shift(1)
            for w in WINDOWS:
                feats[f"opp_last_{w}_allowed_{stat}"] = (
                    prior.rolling(w, min_periods=1).mean().round(3).values
                )
        rows.append(pd.DataFrame(feats))

    out = pd.concat(rows, ignore_index=True)
    log.info("opposing_feature_rows", n=len(out))
    return out


def merge_into_player_games(opp_features: pd.DataFrame):
    """For each player_game, find the opposing team's rolling stats and merge into derived."""
    log.info("loading_player_games_for_merge")
    sql = """
        SELECT pg.player_game_id, pg.opponent_id, pg.game_id, pg.derived
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
    """
    pg_df = pd.read_sql(sql, engine)

    # opp_features.team_id is the team-from-whose-perspective-the-stats-apply.
    # For a player_game, opponent_id is the team they're playing AGAINST,
    # which is the team whose "allowed" features we want.
    merged = pg_df.merge(
        opp_features.rename(columns={"team_id": "opponent_id"}),
        on=["opponent_id", "game_id"],
        how="left",
    )

    log.info("writing_opp_features", rows=len(merged))
    new_cols = [c for c in merged.columns if c.startswith("opp_last_")]
    items = []
    for _, row in merged.iterrows():
        existing = row["derived"] or {}
        if not isinstance(existing, dict):
            existing = json.loads(existing) if existing else {}
        for c in new_cols:
            v = row[c]
            existing[c] = 0.0 if pd.isna(v) else float(v)
        items.append((int(row["player_game_id"]), existing))

    with session_scope() as session:
        for i, (pg_id, derived) in enumerate(items):
            session.execute(text("""
                UPDATE player_games
                SET derived = CAST(:d AS JSONB), updated_at = NOW()
                WHERE player_game_id = :pid
            """), {"d": json.dumps(derived), "pid": pg_id})
            if i % 5000 == 0 and i > 0:
                log.info("write_progress", done=i, total=len(items))

    log.info("opp_features_written", rows=len(items))


def run():
    configure_logging()
    team_df = load_team_game_stats()
    opp_features = compute_opposing_features(team_df)
    merge_into_player_games(opp_features)
    log.info("nba_opposing_team_complete")


if __name__ == "__main__":
    run()
