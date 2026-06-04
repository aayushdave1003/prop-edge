"""Compute trailing rolling features for NBA player-games.

For each NBA player-game, computes:
  - Rolling averages over last N games (5, 10, 20) for each stat
  - Season-to-date averages
  - Rate features for common prop thresholds (over X.5)
  - Days rest, games played this season
  - Per-minute rates (rebounds/min, assists/min) since NBA stats scale with minutes

Lookahead protection: every rolling computation uses shift(1) so the current
game is NEVER included in its own rolling window.

Writes results to player_games.derived JSONB. Idempotent (re-running overwrites).
"""
import json
from datetime import datetime
import pandas as pd
import numpy as np
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


WINDOWS = [5, 10, 20]

# Stats to roll. Both raw counts and key rate stats.
ROLL_STATS = [
    "points", "rebounds", "assists", "threes_made", "threes_attempted",
    "steals", "blocks", "turnovers",
    "fg_made", "fg_attempted", "ft_made", "ft_attempted",
    "off_rebounds", "def_rebounds", "personal_fouls",
    "minutes",
]

# Combo stats we compute on the fly per game then roll
COMBO_STATS = {
    "pts_rebs_asts": ["points", "rebounds", "assists"],
    "pts_rebs": ["points", "rebounds"],
    "pts_asts": ["points", "assists"],
    "rebs_asts": ["rebounds", "assists"],
    "blocks_steals": ["blocks", "steals"],
}

# Thresholds for over-X rate features (matches common PrizePicks lines)
THRESHOLDS = {
    "points": [9.5, 14.5, 19.5, 24.5, 29.5],
    "rebounds": [3.5, 5.5, 7.5, 9.5],
    "assists": [2.5, 4.5, 6.5, 8.5],
    "threes_made": [0.5, 1.5, 2.5, 3.5],
    "pts_rebs_asts": [19.5, 29.5, 39.5],
    "pts_rebs": [14.5, 19.5, 24.5],
    "pts_asts": [14.5, 19.5, 24.5],
    "rebs_asts": [7.5, 9.5, 11.5],
}


def load_player_games() -> pd.DataFrame:
    log.info("loading_nba_player_games")
    sql = """
        SELECT pg.player_game_id, pg.player_id, pg.game_id, g.game_date, g.season,
               pg.stats, pg.minutes_played,
               pg.opponent_id,
               g.season_type
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", n=len(df))
    return df


def explode_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = pd.json_normalize(df["stats"])
    out = df.drop(columns=["stats"]).reset_index(drop=True)
    for col in ROLL_STATS:
        if col in stats.columns:
            out[col] = pd.to_numeric(stats[col], errors="coerce").fillna(0)
        else:
            out[col] = 0
    # NBA box scores store fg3_made/fg3_attempted; alias to threes_* for consistency
    if "threes_made" not in stats.columns and "fg3_made" in stats.columns:
        out["threes_made"] = pd.to_numeric(stats["fg3_made"], errors="coerce").fillna(0)
    if "threes_attempted" not in stats.columns and "fg3_attempted" in stats.columns:
        out["threes_attempted"] = pd.to_numeric(stats["fg3_attempted"], errors="coerce").fillna(0)
    # Compute combo stats per game
    for combo_name, components in COMBO_STATS.items():
        out[combo_name] = sum(out[c] for c in components)
    return out


def compute_rolling_for_player(group: pd.DataFrame) -> pd.DataFrame:
    """For a single player's game history, compute all rolling features."""
    g = group.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
    feats = {"player_game_id": g["player_game_id"].values}

    # Days rest (since previous game in our data)
    prior_dates = g["game_date"].shift(1)
    days_rest = (g["game_date"] - prior_dates).dt.days.fillna(-1).astype(int)
    feats["days_rest"] = days_rest.values

    # Games played this season (count of prior games this season, inclusive of THIS one)
    season_marker = (g["season"] != g["season"].shift(1)).cumsum()
    games_played = g.groupby(season_marker).cumcount()
    feats["games_played_season"] = games_played.values

    # Playoff context flags
    feats["is_playoff"] = (g["season_type"].isin(["playoffs", "play_in"])).astype(int).values

    # Series-specific features: rolling avg vs same opponent in current season
    # series_game_num = how many games vs this opponent so far (shift(1) = prior)
    series_key = g["season"].astype(str) + "_" + g["opponent_id"].fillna(-1).astype(str)
    feats["series_game_num"] = g.groupby(series_key).cumcount().values  # 0-indexed prior games

    # Series rolling averages for key stats (points, rebounds, assists)
    for stat in ["points", "rebounds", "assists"]:
        if stat not in g.columns:
            feats[f"series_avg_{stat}"] = np.zeros(len(g))
            continue
        series_avgs = np.zeros(len(g))
        for key in series_key.unique():
            mask = series_key == key
            vals = g.loc[mask, stat].shift(1)
            series_avgs[mask.values] = vals.expanding().mean().fillna(0).values
        feats[f"series_avg_{stat}"] = series_avgs.round(4)

    # All stats: rolling averages over prior games
    all_stat_cols = ROLL_STATS + list(COMBO_STATS.keys())
    for stat in all_stat_cols:
        prior_values = g[stat].shift(1)
        for w in WINDOWS:
            avg = prior_values.rolling(w, min_periods=1).mean().fillna(0)
            feats[f"last_{w}_avg_{stat}"] = avg.round(4).values

        # Season-to-date average (excluding current game)
        cum_sum = g.groupby(season_marker)[stat].apply(
            lambda s: s.shift(1).expanding().mean()
        ).reset_index(level=0, drop=True).fillna(0)
        feats[f"season_avg_{stat}"] = cum_sum.round(4).values

    # Threshold-cross rate features (over last 10 games)
    for stat, thresholds in THRESHOLDS.items():
        prior_values = g[stat].shift(1)
        for thr in thresholds:
            recent_window = prior_values.rolling(10, min_periods=1)
            rate = recent_window.apply(lambda x: (x > thr).mean(), raw=True).fillna(0)
            feats[f"last_10_rate_over_{thr}_{stat}"] = rate.round(4).values

    return pd.DataFrame(feats)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    log.info("computing_rolling_features")
    results = []
    for pid, group in df.groupby("player_id", group_keys=False):
        results.append(compute_rolling_for_player(group))
    out = pd.concat(results, ignore_index=True)
    feature_count = len([c for c in out.columns if c != "player_game_id"])
    log.info("rolling_features_computed", rows=len(out), features=feature_count)
    return out


def write_to_derived(feature_df: pd.DataFrame, batch_size: int = 5000):
    log.info("writing_to_derived", rows=len(feature_df))
    feature_cols = [c for c in feature_df.columns if c != "player_game_id"]
    items = []
    for _, row in feature_df.iterrows():
        feat = {col: (0 if pd.isna(row[col]) else (
            int(row[col]) if isinstance(row[col], (np.integer,)) else float(row[col])
        )) for col in feature_cols}
        items.append((int(row["player_game_id"]), feat))

    with session_scope() as session:
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            for pg_id, feat in batch:
                session.execute(text("""
                    UPDATE player_games
                    SET derived = CAST(:f AS JSONB), updated_at = NOW()
                    WHERE player_game_id = :pid
                """), {"f": json.dumps(feat), "pid": pg_id})
            if (i // batch_size) % 5 == 0:
                log.info("write_progress", done=min(i + batch_size, len(items)),
                         total=len(items))


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('nba_rolling', :s, 'running')
            RETURNING run_id
        """), {"s": started}).scalar()

    df = load_player_games()
    df = explode_stats(df)
    feature_df = compute_all(df)
    write_to_derived(feature_df)

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(feature_df), "rid": run_id})

    log.info("nba_rolling_complete", updated=len(feature_df))


if __name__ == "__main__":
    run()
