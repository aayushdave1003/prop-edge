"""Add opposing-team batting lineup quality features for pitchers.

For each pitcher-game, computes features about the lineup they faced:
- Team's rolling K rate
- Team's rolling avg runs per game
- Team's rolling avg total bases per game
This is the equivalent of opposing-pitcher features for batters, flipped.
"""
from datetime import datetime
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived


def load_team_batting_aggregates():
    """For every game, compute the offensive team's total batting stats."""
    log.info("loading_team_batting_per_game")
    sql = """
        SELECT pg.game_id, pg.team_id, g.game_date, g.season,
               SUM((pg.stats->>'strikeouts')::int) AS team_k,
               SUM((pg.stats->>'plate_appearances')::int) AS team_pa,
               SUM((pg.stats->>'hits')::int) AS team_hits,
               SUM((pg.stats->>'total_bases')::int) AS team_tb,
               SUM((pg.stats->>'runs')::int) AS team_runs,
               SUM((pg.stats->>'walks')::int) AS team_walks
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'plate_appearances')::int > 0
        GROUP BY pg.game_id, pg.team_id, g.game_date, g.season
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded_team_games", n=len(df))
    return df


def compute_team_rolling(team_df: pd.DataFrame) -> pd.DataFrame:
    """For each team, rolling K rate and offensive stats over prior games."""
    team_df = team_df.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)
    results = []
    for tid, g in team_df.groupby("team_id", group_keys=False):
        g = g.sort_values(["game_date", "game_id"]).reset_index(drop=True)
        prior_k = g["team_k"].shift(1)
        prior_pa = g["team_pa"].shift(1)
        prior_tb = g["team_tb"].shift(1)
        prior_runs = g["team_runs"].shift(1)
        prior_walks = g["team_walks"].shift(1)

        feats = {"game_id": g["game_id"].values, "team_id": g["team_id"].values}
        for w in [10, 20]:
            sum_k = prior_k.rolling(w, min_periods=1).sum()
            sum_pa = prior_pa.rolling(w, min_periods=1).sum()
            sum_tb = prior_tb.rolling(w, min_periods=1).sum()
            sum_runs = prior_runs.rolling(w, min_periods=1).sum()
            sum_walks = prior_walks.rolling(w, min_periods=1).sum()
            n = pd.Series(range(1, len(g) + 1)).clip(upper=w)

            feats[f"lineup_last_{w}_k_rate"] = (
                (sum_k / sum_pa).where(sum_pa > 0, 0).fillna(0).values
            )
            feats[f"lineup_last_{w}_avg_runs"] = (sum_runs / n).fillna(0).values
            feats[f"lineup_last_{w}_avg_tb"] = (sum_tb / n).fillna(0).values
            feats[f"lineup_last_{w}_walk_rate"] = (
                (sum_walks / sum_pa).where(sum_pa > 0, 0).fillna(0).values
            )
        results.append(pd.DataFrame(feats))
    out = pd.concat(results, ignore_index=True)
    log.info("computed_team_rolling", rows=len(out))
    return out


def attach_to_pitchers(team_rolling: pd.DataFrame):
    """Merge team-rolling features onto each pitcher-game by opponent_id."""
    log.info("loading_pitcher_games")
    pitcher_df = pd.read_sql("""
        SELECT pg.player_game_id, pg.game_id, pg.opponent_id
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code='mlb'
          AND (pg.stats->>'batters_faced')::int > 0
    """, engine)
    joined = pitcher_df.merge(
        team_rolling,
        left_on=["game_id", "opponent_id"],
        right_on=["game_id", "team_id"],
        how="left",
    )
    log.info("joined", matched=joined["lineup_last_10_k_rate"].notna().sum(),
             total=len(joined))
    return joined


def merge_into_derived(features_df: pd.DataFrame):
    feature_cols = [c for c in features_df.columns if c.startswith("lineup_last_")]
    items = []
    for _, row in features_df.iterrows():
        feat = {col: (round(float(row[col]), 4) if pd.notna(row[col]) else 0)
                for col in feature_cols}
        items.append((int(row["player_game_id"]), feat))
    write_derived(items, mode="merge", label="mlb_opposing_lineup")


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('mlb_opposing_lineup', :s, 'running')
            RETURNING run_id
        """), {"s": started}).scalar()

    team_df = load_team_batting_aggregates()
    team_rolling = compute_team_rolling(team_df)
    joined = attach_to_pitchers(team_rolling)
    merge_into_derived(joined)

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(joined), "rid": run_id})
    log.info("opposing_lineup_complete", updated=len(joined))


if __name__ == "__main__":
    run()
