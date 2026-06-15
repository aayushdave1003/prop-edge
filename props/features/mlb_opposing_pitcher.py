"""Add opposing-pitcher features to batters' player_games.derived JSONB.

For each batter-game, computes features describing the starting pitcher
they faced: that pitcher's rolling K-rate, hits allowed/9, walks/9, ERA.
The starter is identified as the pitcher with the most batters faced
from the opposing team in that game.

Critical: opposing-pitcher rolling features use only games PRIOR to this
one (same lookahead discipline as the rolling features module).
"""
from datetime import datetime
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

WINDOWS = [5, 10]


def load_all_games_with_pitchers():
    """Pull every pitcher-game (i.e., player_games rows where the pitcher faced batters).

    Returns DataFrame with one row per pitching appearance, with the stats
    that matter for computing pitcher quality.
    """
    log.info("loading_pitcher_games")
    sql = """
        SELECT pg.player_game_id, pg.player_id, pg.game_id, pg.team_id,
               g.game_date, g.season,
               (pg.stats->>'batters_faced')::int AS batters_faced,
               (pg.stats->>'outs_recorded')::int AS outs_recorded,
               (pg.stats->>'strikeouts_pitcher')::int AS k,
               (pg.stats->>'hits_allowed')::int AS h_allowed,
               (pg.stats->>'walks_allowed')::int AS bb_allowed,
               (pg.stats->>'earned_runs')::int AS er,
               (pg.stats->>'home_runs_allowed')::int AS hr_allowed
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'batters_faced')::int > 0
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded_pitcher_appearances", n=len(df))
    return df


def compute_pitcher_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """For each pitcher, compute rolling features over PRIOR appearances only."""
    df = df.sort_values(["player_id", "game_date", "player_game_id"]).reset_index(drop=True)
    out_features = []

    for pid, group in df.groupby("player_id", group_keys=False):
        g = group.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)

        # Shift by 1 so current game is excluded
        prior_bf = g["batters_faced"].shift(1)
        prior_outs = g["outs_recorded"].shift(1)
        prior_k = g["k"].shift(1)
        prior_h = g["h_allowed"].shift(1)
        prior_bb = g["bb_allowed"].shift(1)
        prior_er = g["er"].shift(1)

        feats = {"player_game_id": g["player_game_id"].values}

        for w in WINDOWS:
            # Rolling sums for prior W appearances
            sum_bf = prior_bf.rolling(w, min_periods=1).sum()
            sum_outs = prior_outs.rolling(w, min_periods=1).sum()
            sum_k = prior_k.rolling(w, min_periods=1).sum()
            sum_h = prior_h.rolling(w, min_periods=1).sum()
            sum_bb = prior_bb.rolling(w, min_periods=1).sum()
            sum_er = prior_er.rolling(w, min_periods=1).sum()

            # Rate stats per batter faced and per 9 innings (= 27 outs)
            k_rate = (sum_k / sum_bf).where(sum_bf > 0, 0).fillna(0)
            h_per_9 = (sum_h * 27 / sum_outs).where(sum_outs > 0, 0).fillna(0)
            bb_per_9 = (sum_bb * 27 / sum_outs).where(sum_outs > 0, 0).fillna(0)
            era = (sum_er * 27 / sum_outs).where(sum_outs > 0, 0).fillna(0)
            avg_k_per_start = (sum_k / w).fillna(0)
            avg_outs_per_start = (sum_outs / w).fillna(0)

            feats[f"pitcher_last_{w}_k_rate"] = k_rate.values
            feats[f"pitcher_last_{w}_h_per_9"] = h_per_9.values
            feats[f"pitcher_last_{w}_bb_per_9"] = bb_per_9.values
            feats[f"pitcher_last_{w}_era"] = era.values
            feats[f"pitcher_last_{w}_avg_k"] = avg_k_per_start.values
            feats[f"pitcher_last_{w}_avg_outs"] = avg_outs_per_start.values

        out_features.append(pd.DataFrame(feats))

    result = pd.concat(out_features, ignore_index=True)
    log.info("pitcher_rolling_computed", rows=len(result),
             feature_count=len(result.columns) - 1)
    return result


def identify_starters(pitcher_df: pd.DataFrame) -> pd.DataFrame:
    """For each (game, team), pick the pitcher with the most batters faced — the starter."""
    # Rank pitchers within each (game, team) by batters faced
    pitcher_df = pitcher_df.copy()
    pitcher_df["rank_within_team_game"] = (
        pitcher_df.groupby(["game_id", "team_id"])["batters_faced"]
        .rank(method="first", ascending=False)
    )
    starters = pitcher_df[pitcher_df["rank_within_team_game"] == 1].copy()
    starters = starters[["game_id", "team_id", "player_id", "player_game_id"]]
    starters.columns = ["game_id", "pitcher_team_id", "pitcher_player_id",
                        "pitcher_player_game_id"]
    log.info("identified_starters", n=len(starters))
    return starters


def attach_opposing_pitcher_features(starters: pd.DataFrame,
                                     pitcher_features: pd.DataFrame) -> pd.DataFrame:
    """Join starter info with their rolling features (by player_game_id)."""
    starters_with_feats = starters.merge(
        pitcher_features,
        left_on="pitcher_player_game_id",
        right_on="player_game_id",
        how="left",
    ).drop(columns=["player_game_id"])
    return starters_with_feats


def build_batter_opposing_features(starters_with_feats: pd.DataFrame) -> dict[int, dict]:
    """For each batter-game, find the OPPOSING team's starter and attach their features.

    Returns {batter_player_game_id: {opp_pitcher_feature_dict}}.
    """
    log.info("loading_batter_games")
    sql = """
        SELECT pg.player_game_id, pg.game_id, pg.team_id, pg.opponent_id
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'plate_appearances')::int > 0
    """
    batters = pd.read_sql(sql, engine)
    log.info("loaded_batter_games", n=len(batters))

    # Join: a batter's opponent_id is the pitcher_team_id
    joined = batters.merge(
        starters_with_feats,
        left_on=["game_id", "opponent_id"],
        right_on=["game_id", "pitcher_team_id"],
        how="left",
    )
    log.info("joined_batter_to_opposing_starter",
             matched=joined["pitcher_player_id"].notna().sum(),
             total=len(joined))

    feature_cols = [c for c in joined.columns if c.startswith("pitcher_last_")]
    out = {}
    for _, row in joined.iterrows():
        pg_id = int(row["player_game_id"])
        feat = {}
        for col in feature_cols:
            val = row[col]
            if pd.isna(val):
                feat[col] = 0
            else:
                feat[col] = round(float(val), 4)
        # Also store the opposing pitcher's ID for joins later
        if pd.notna(row["pitcher_player_id"]):
            feat["opposing_pitcher_id"] = int(row["pitcher_player_id"])
        out[pg_id] = feat
    return out


def merge_into_derived(opp_features: dict[int, dict]):
    """Merge new features into the existing derived JSONB without overwriting."""
    write_derived(opp_features.items(), mode="merge", label="mlb_opposing_pitcher")


def run():
    configure_logging()
    started = datetime.now()

    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('mlb_opposing_pitcher', :s, 'running')
            RETURNING run_id
        """), {"s": started}).scalar()

    pitcher_df = load_all_games_with_pitchers()
    pitcher_features = compute_pitcher_rolling(pitcher_df)
    starters = identify_starters(pitcher_df)
    starters_with_feats = attach_opposing_pitcher_features(starters, pitcher_features)
    opp_features = build_batter_opposing_features(starters_with_feats)
    merge_into_derived(opp_features)

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(opp_features), "rid": run_id})

    log.info("opposing_pitcher_features_complete", updated=len(opp_features))


if __name__ == "__main__":
    run()
