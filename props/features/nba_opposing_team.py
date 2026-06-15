"""Compute opposing-team rolling features for NBA player-games.

For each NBA player-game, attaches the opposing team's prior-10-game averages:
  - Points allowed per game (opp_drtg_proxy)
  - Pace (combined FGA + 0.4 * FTA + TOV - OREB, per Dean Oliver formula)
  - 3-point makes allowed
  - Rebounds allowed

Lookahead-safe: uses opposing team's prior games only.
Writes to player_games.derived JSONB (merges with existing keys).
"""
import pandas as pd
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived


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


def compute_team_pace_features(team_df: pd.DataFrame) -> pd.DataFrame:
    """For each (team, game), compute rolling avg of game total (pts scored + pts allowed)
    and own team pts. These capture pace/tempo independent of individual player output.
    """
    log.info("computing_team_pace_features")

    # Join team_df to itself on game_id to get both teams' pts in same game
    merged = team_df.merge(
        team_df[["game_id", "team_id", "pts_scored"]].rename(
            columns={"team_id": "opp_tid", "pts_scored": "opp_pts"}
        ),
        on="game_id",
    )
    # Keep only rows where opp_tid is actually the opponent
    merged = merged[merged["team_id"] != merged["opp_tid"]]
    merged["game_total"] = merged["pts_scored"] + merged["opp_pts"]

    rows = []
    for tid, group in merged.groupby("team_id"):
        g = group.sort_values(["game_date", "game_id"]).drop_duplicates(
            subset=["game_id"]
        ).reset_index(drop=True)

        prior_total    = g["game_total"].shift(1)
        prior_pts      = g["pts_scored"].shift(1)

        feats = {
            "team_id": g["team_id"].values,
            "game_id": g["game_id"].values,
        }
        for w in [5, 10]:
            feats[f"team_last_{w}_avg_game_total"] = (
                prior_total.rolling(w, min_periods=1).mean().round(2).values
            )
            feats[f"team_last_{w}_avg_pts_scored"] = (
                prior_pts.rolling(w, min_periods=1).mean().round(2).values
            )
        rows.append(pd.DataFrame(feats))

    out = pd.concat(rows, ignore_index=True)
    log.info("team_pace_feature_rows", n=len(out))
    return out


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


def merge_into_player_games(opp_features: pd.DataFrame, pace_features: pd.DataFrame):
    """For each player_game, merge opposing team rolling stats and own team pace features."""
    log.info("loading_player_games_for_merge")
    sql = """
        SELECT pg.player_game_id, pg.team_id, pg.opponent_id, pg.game_id, pg.derived
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
    """
    pg_df = pd.read_sql(sql, engine)

    # Merge opposing team "allowed" features
    merged = pg_df.merge(
        opp_features.rename(columns={"team_id": "opponent_id"}),
        on=["opponent_id", "game_id"],
        how="left",
    )

    # Merge own team pace features (game total, team pts scored)
    merged = merged.merge(
        pace_features,
        on=["team_id", "game_id"],
        how="left",
    )

    log.info("writing_opp_features", rows=len(merged))
    new_cols = [c for c in merged.columns if c.startswith("opp_last_") or c.startswith("team_last_")]
    items = []
    for _, row in merged.iterrows():
        patch = {c: (0.0 if pd.isna(row[c]) else float(row[c])) for c in new_cols}
        items.append((int(row["player_game_id"]), patch))
    write_derived(items, mode="merge", label="nba_opposing_team")

    log.info("opp_features_written", rows=len(items))


def run():
    configure_logging()
    team_df = load_team_game_stats()
    opp_features = compute_opposing_features(team_df)
    pace_features = compute_team_pace_features(team_df)
    merge_into_player_games(opp_features, pace_features)
    log.info("nba_opposing_team_complete")


if __name__ == "__main__":
    run()
