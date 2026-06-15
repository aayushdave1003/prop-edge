"""Opponent-adjusted (strength-of-schedule) features for MLB batters.

The batter models already know TOMORROW's pitcher quality (the pitcher_last_*
keys), but a batter's own rolling form (last_10_avg_hits, …) is RAW — a hot streak
against aces counts the same as one against soft bullpens. This rolls the quality
of the pitchers the batter ACTUALLY FACED over his prior games (their rolling ERA
and K-rate, already attached to each batter-game's derived by
mlb_opposing_pitcher), so the model can discount form built against weak pitching
and interact it with the raw form keys.

Prior-games-only (shift(1)) — no lookahead. Depends on mlb_opposing_pitcher having
run first (it reads pitcher_last_10_era / pitcher_last_10_k_rate from derived).
The inference mirror lives in props.features.inference.batter_features and MUST
match the rolling here (mean of the last 10 prior games), since the A/B gate reads
the stored value and won't catch a live-inference skew.
"""
import pandas as pd
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

W = 10


def run():
    configure_logging()
    df = pd.read_sql(text("""
        SELECT pg.player_game_id, pg.player_id, g.game_date,
               (pg.derived->>'pitcher_last_10_era')::float    AS era,
               (pg.derived->>'pitcher_last_10_k_rate')::float AS krate
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb'
          AND (pg.stats->>'plate_appearances')::int > 0
          AND pg.derived ? 'pitcher_last_10_era'
    """), engine)
    if df.empty:
        log.warning("batter_sos_no_input",
                    hint="run mlb_opposing_pitcher first (needs pitcher_last_10_era)")
        return
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values(["player_id", "game_date", "player_game_id"]).reset_index(drop=True)

    feats: dict[int, dict] = {}
    for _, g in df.groupby("player_id", group_keys=False):
        g = g.sort_values(["game_date", "player_game_id"])
        era = g["era"].shift(1).rolling(W, min_periods=1).mean()
        kr = g["krate"].shift(1).rolling(W, min_periods=1).mean()
        for pgid, e, k in zip(g["player_game_id"], era, kr):
            feats[int(pgid)] = {
                "last_10_avg_faced_era": round(float(e), 4) if pd.notna(e) else 0,
                "last_10_avg_faced_k_rate": round(float(k), 4) if pd.notna(k) else 0,
            }
    write_derived(feats.items(), mode="merge", label="mlb_batter_sos")
    log.info("batter_sos_complete", updated=len(feats))


if __name__ == "__main__":
    run()
