"""Compute home/away rolling features for NBA player-games.

For each player-game, computes their last-10 average stats split by venue:
  - last_10_avg_points_home / last_10_avg_points_away
  - same for rebounds, assists, threes_made, minutes

Lookahead-safe: uses shift(1) before rolling.
Writes to player_games.derived JSONB (merges).
"""
import pandas as pd
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived, feat_dict


STATS_TO_SPLIT = ["points", "rebounds", "assists", "threes_made", "minutes"]
WINDOW = 10


def load_data():
    log.info("loading_nba_player_games")
    sql = """
        SELECT pg.player_game_id, pg.player_id, pg.is_home,
               g.game_date, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'nba'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    stats = pd.json_normalize(df["stats"])
    for s in STATS_TO_SPLIT:
        df[s] = pd.to_numeric(stats[s], errors="coerce").fillna(0) if s in stats.columns else 0
    df = df.drop(columns=["stats"])
    log.info("loaded", n=len(df))
    return df


def compute_splits(df):
    """For each player, compute rolling avg of each stat for home and away separately."""
    log.info("computing_home_away_splits")
    results = []
    for pid, group in df.groupby("player_id", group_keys=False):
        g = group.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}
        for stat in STATS_TO_SPLIT:
            # Home version: only home prior games count
            home_mask = g["is_home"]
            home_vals = g[stat].where(home_mask).shift(1)
            # Restrict to last 10 home games
            home_recent = home_vals.rolling(WINDOW * 3, min_periods=1).apply(
                lambda x: pd.Series(x).dropna().tail(WINDOW).mean() if pd.Series(x).dropna().any() else 0,
                raw=False
            ).fillna(0)
            feats[f"last_{WINDOW}_avg_{stat}_home"] = home_recent.round(3).values

            # Away version
            away_mask = ~g["is_home"]
            away_vals = g[stat].where(away_mask).shift(1)
            away_recent = away_vals.rolling(WINDOW * 3, min_periods=1).apply(
                lambda x: pd.Series(x).dropna().tail(WINDOW).mean() if pd.Series(x).dropna().any() else 0,
                raw=False
            ).fillna(0)
            feats[f"last_{WINDOW}_avg_{stat}_away"] = away_recent.round(3).values

        results.append(pd.DataFrame(feats))

    out = pd.concat(results, ignore_index=True)
    feat_count = len([c for c in out.columns if c != "player_game_id"])
    log.info("home_away_features_computed", rows=len(out), features=feat_count)
    return out


def write_to_derived(feature_df):
    feature_cols = [c for c in feature_df.columns if c != "player_game_id"]
    items = [(int(row["player_game_id"]), feat_dict(row, feature_cols))
             for _, row in feature_df.iterrows()]
    write_derived(items, mode="merge", label="nba_home_away")


def run():
    configure_logging()
    df = load_data()
    splits = compute_splits(df)
    write_to_derived(splits)
    log.info("nba_home_away_complete")


if __name__ == "__main__":
    run()
