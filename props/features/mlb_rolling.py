"""Compute player-level rolling features for MLB and store in player_games.derived.

For each (player, game), looks at prior games only and computes rolling-window
stats. Strictly no lookahead — at row N, only rows 1..N-1 are used.
"""
import json
from datetime import datetime
import pandas as pd
import numpy as np
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

# Stats to compute rolling features for.
# Each player will have features for all of these — pitchers will have
# zeros for batter stats and vice versa, which is fine.
BATTING_STATS = [
    "hits", "total_bases", "rbis", "runs", "home_runs",
    "strikeouts", "walks", "at_bats",
]
PITCHING_STATS = [
    "strikeouts_pitcher", "outs_recorded", "earned_runs",
    "walks_allowed", "hits_allowed", "batters_faced",
]
ALL_STATS = BATTING_STATS + PITCHING_STATS

# Rolling windows
WINDOWS = [5, 10, 20]

# Key prop thresholds — rate of going over these in recent games
# These mirror common PrizePicks lines
THRESHOLDS = {
    "hits": [0.5, 1.5, 2.5],
    "total_bases": [1.5, 2.5, 3.5],
    "rbis": [0.5, 1.5],
    "home_runs": [0.5],
    "strikeouts_pitcher": [4.5, 5.5, 6.5, 7.5],
}


def load_player_games() -> pd.DataFrame:
    """Pull all MLB player-games joined with game dates into a DataFrame."""
    log.info("loading_player_games_into_memory")
    sql = """
        SELECT pg.player_game_id, pg.player_id, pg.game_id,
               g.game_date, g.season,
               pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """
    df = pd.read_sql(sql, engine)
    log.info("loaded", rows=len(df))

    # Unpack the JSONB stats into columns
    stats_df = pd.json_normalize(df["stats"])
    # Coerce all stat columns to numeric (some may be None)
    for col in ALL_STATS:
        if col in stats_df.columns:
            stats_df[col] = pd.to_numeric(stats_df[col], errors="coerce").fillna(0)
        else:
            stats_df[col] = 0

    out = pd.concat([df.drop(columns=["stats"]).reset_index(drop=True),
                     stats_df[ALL_STATS].reset_index(drop=True)], axis=1)
    out["game_date"] = pd.to_datetime(out["game_date"])
    return out


def compute_rolling_features(group: pd.DataFrame) -> pd.DataFrame:
    """For one player's games in chronological order, compute rolling features.

    Critical: every feature is computed using ONLY prior rows (shift(1) first,
    then rolling). This prevents lookahead.
    """
    group = group.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
    features = {}

    # Days rest since last game
    features["days_rest"] = group["game_date"].diff().dt.days.fillna(-1).astype(int)

    # Games played this season (1-indexed at game time, so prior count)
    features["games_played_season"] = (
        group.groupby("season").cumcount()
    )  # 0 for first game of season, 1 for second, etc. = number of prior games

    # Rolling averages and thresholds for each stat
    for stat in ALL_STATS:
        # Use shift(1) so we exclude the current game from its own rolling window
        prior = group[stat].shift(1)

        for w in WINDOWS:
            features[f"last_{w}_avg_{stat}"] = (
                prior.rolling(w, min_periods=1).mean().fillna(0)
            )

        # Season-to-date average (excluding current game)
        features[f"season_avg_{stat}"] = (
            group.groupby("season")[stat].transform(
                lambda s: s.shift(1).expanding().mean()
            ).fillna(0)
        )

        # Rate over threshold (only for stats we have thresholds defined for)
        if stat in THRESHOLDS:
            for thr in THRESHOLDS[stat]:
                over_indicator = (prior > thr).astype(float)
                features[f"last_10_rate_over_{thr}_{stat}"] = (
                    over_indicator.rolling(10, min_periods=1).mean().fillna(0)
                )

    feature_df = pd.DataFrame(features)
    feature_df["player_game_id"] = group["player_game_id"].values
    return feature_df


def features_to_jsonb_dicts(features_df: pd.DataFrame) -> dict[int, dict]:
    """Convert wide feature DataFrame to {player_game_id: feature_dict}."""
    feature_cols = [c for c in features_df.columns if c != "player_game_id"]
    out = {}
    for _, row in features_df.iterrows():
        pg_id = int(row["player_game_id"])
        feat = {}
        for col in feature_cols:
            val = row[col]
            if pd.isna(val):
                feat[col] = 0
            elif isinstance(val, (np.integer, np.int64)):
                feat[col] = int(val)
            elif isinstance(val, (np.floating, np.float64)):
                feat[col] = round(float(val), 4)
            else:
                feat[col] = val
        out[pg_id] = feat
    return out


def write_features_to_db(feature_map: dict[int, dict]):
    """Update player_games.derived with computed features (batched + resilient)."""
    write_derived(feature_map.items(), mode="replace", label="mlb_rolling")


def run():
    configure_logging()
    started = datetime.now()

    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('mlb_rolling_features', :s, 'running')
            RETURNING run_id
        """), {"s": started}).scalar()

    df = load_player_games()
    log.info("computing_features_per_player", players=df["player_id"].nunique())

    # Process each player's games
    all_features = []
    grouped = df.groupby("player_id", group_keys=False)
    for i, (pid, group) in enumerate(grouped):
        try:
            feats = compute_rolling_features(group)
            all_features.append(feats)
        except Exception as e:
            log.error("feature_compute_failed", player_id=pid, error=str(e))
        if (i + 1) % 500 == 0:
            log.info("feature_progress", players_done=i + 1)

    features_df = pd.concat(all_features, ignore_index=True)
    log.info("features_computed", rows=len(features_df),
             feature_count=len(features_df.columns) - 1)

    feature_map = features_to_jsonb_dicts(features_df)
    write_features_to_db(feature_map)

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(feature_map), "rid": run_id})

    log.info("rolling_features_complete", updated=len(feature_map))


if __name__ == "__main__":
    run()
