"""Compute batter-vs-pitcher career history for MLB player-games.

For each batter-game where we know the opposing pitcher, computes the batter's
lifetime stats vs that specific pitcher (using only PRIOR games — lookahead-safe):
  - bvp_career_pa (plate appearances)
  - bvp_career_ab (at bats)
  - bvp_career_hits
  - bvp_career_strikeouts
  - bvp_career_walks
  - bvp_career_total_bases

Pairs are sparse (most batter-pitcher pairs have <5 PAs); features may be 0 for
matchups never seen before. The model will learn from cases where the pair has
history.

Writes to player_games.derived JSONB (merges).
"""
import json
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived


def run():
    configure_logging()
    log.info("loading_mlb_batter_pitcher_pairs")

    # Pull batter games with their game's opposing starting pitcher
    sql = """
        SELECT pg.player_game_id, pg.player_id AS batter_id,
               g.game_date,
               (g.context->>'opposing_pitcher_id')::int AS pitcher_id,
               (pg.stats->>'plate_appearances')::int AS pa,
               (pg.stats->>'at_bats')::int AS ab,
               (pg.stats->>'hits')::int AS h,
               (pg.stats->>'strikeouts')::int AS k,
               (pg.stats->>'walks')::int AS bb,
               (pg.stats->>'total_bases')::int AS tb,
               pg.derived
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND g.context ? 'opposing_pitcher_id'
          AND pg.stats ? 'plate_appearances'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded_batter_games_with_pitcher", n=len(df))

    if df.empty:
        log.warning("no_rows_with_pitcher_context_skipping")
        return

    # Sort and compute expanding sum per (batter, pitcher) pair, then shift(1)
    df = df.sort_values(["batter_id", "pitcher_id", "game_date", "player_game_id"])
    stat_cols = ["pa", "ab", "h", "k", "bb", "tb"]
    grouped = df.groupby(["batter_id", "pitcher_id"], group_keys=False)
    for col in stat_cols:
        df[f"bvp_career_{col}"] = grouped[col].apply(
            lambda s: s.shift(1).fillna(0).cumsum()
        ).reset_index(level=[0, 1], drop=True) if False else (
            grouped[col].cumsum().sub(df[col], fill_value=0)
        )
    # bvp_career_X is now (cumsum so far INCLUDING this game) - (this game) = prior PAs

    # Write
    log.info("writing_bvp_features", rows=len(df))
    name_map = {
        "bvp_career_pa": "bvp_career_pa",
        "bvp_career_ab": "bvp_career_ab",
        "bvp_career_h": "bvp_career_hits",
        "bvp_career_k": "bvp_career_strikeouts",
        "bvp_career_bb": "bvp_career_walks",
        "bvp_career_tb": "bvp_career_total_bases",
    }
    items = [(int(row["player_game_id"]),
              {dst: (int(row[src]) if pd.notna(row[src]) else 0)
               for src, dst in name_map.items()})
             for _, row in df.iterrows()]
    write_derived(items, mode="merge", label="mlb_batter_vs_pitcher")

    log.info("mlb_batter_vs_pitcher_complete", rows=len(df))


if __name__ == "__main__":
    run()
