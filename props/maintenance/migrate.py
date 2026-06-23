"""Tiny forward-only migration runner (E13).

Replaces the scattered ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` calls that
were sprinkled through the dashboard startup and log_picks. Migrations are
idempotent (IF NOT EXISTS) and tracked in ``schema_migrations`` so they run
once. DDL needs autocommit — it can't run inside a regular transaction.

Run standalone:  python -m props.maintenance.migrate
Or in code:      from props.maintenance.migrate import run_migrations; run_migrations()
"""
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import log

# (id, sql) — append-only. Keep statements idempotent. Multiple statements per
# migration are separated by ';'.
MIGRATIONS: list[tuple[str, str]] = [
    ("0001_picks_line_movement",
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS line_open NUMERIC(8,3);"
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS line_movement NUMERIC(6,3)"),
    ("0002_picks_market_edge",
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS market_edge NUMERIC(6,4)"),
    ("0003_picks_injury_flag",
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS injury_flag NUMERIC(6,1) DEFAULT 0"),
    ("0004_player_injuries",
     "CREATE TABLE IF NOT EXISTS player_injuries ("
     "  player_name TEXT NOT NULL,"
     "  team_name TEXT NOT NULL,"
     "  sport_code TEXT NOT NULL DEFAULT 'nba',"
     "  status TEXT NOT NULL,"
     "  short_comment TEXT,"
     "  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
     "  PRIMARY KEY (player_name, sport_code, fetched_at));"
     "ALTER TABLE player_injuries ADD COLUMN IF NOT EXISTS sport_code TEXT NOT NULL DEFAULT 'nba';"
     "CREATE INDEX IF NOT EXISTS idx_injuries_sport_player_recent"
     "  ON player_injuries (sport_code, player_name, fetched_at DESC)"),
    ("0005_picks_line_close",
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS line_close NUMERIC(8,3)"),
    # The player_games id sequence drifted behind max(player_game_id) (a bulk
    # load preserved ids without setval), so new box-score inserts collided on
    # the pkey and rolled back — silently breaking ingestion for some games.
    # Reset it to max so the serial advances cleanly again.
    ("0006_fix_player_games_seq",
     "SELECT setval(pg_get_serial_sequence('player_games','player_game_id'),"
     "              (SELECT COALESCE(MAX(player_game_id), 1) FROM player_games))"),
    # Daily walk-forward backtest snapshots — one row per run_date so the
    # recommended-tier edge, calibration, and cutoff-fit trends accumulate over
    # time (the dashboard + Discord digest read the history).
    ("0007_backtest_daily",
     "CREATE TABLE IF NOT EXISTS backtest_daily ("
     "  run_date     DATE PRIMARY KEY,"
     "  window_days  INT  NOT NULL,"
     "  rec_n        INT,"
     "  rec_w        INT,"
     "  rec_l        INT,"
     "  rec_winrate  DOUBLE PRECISION,"
     "  rec_roi_2pick DOUBLE PRECISION,"
     "  all_n        INT,"
     "  all_winrate  DOUBLE PRECISION,"
     "  brier        DOUBLE PRECISION,"
     "  detail       JSONB,"
     "  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW())"),
    # Model/market blend: picks.model_prob now stores the BLENDED probability
    # (what selection, cutoffs, calibration, and display all read). The raw model
    # output is preserved in model_prob_raw (so the blend weight stays tunable)
    # and the real market-implied prob for the pick's side in market_prob (NULL
    # when no line). See props.models.blend_weights.
    ("0008_picks_blend_cols",
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS model_prob_raw NUMERIC(8,4);"
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS market_prob NUMERIC(8,4)"),
    # Soft-line finder: where a PrizePicks line is softer than the sharp market's
    # read (converted to the PP line via the market-implied Poisson lambda) — a
    # market-grounded +EV edge independent of the model. One snapshot per day.
    ("0009_soft_lines",
     "CREATE TABLE IF NOT EXISTS soft_lines ("
     "  run_date        DATE NOT NULL,"
     "  sport_code      TEXT,"
     "  player_name     TEXT NOT NULL,"
     "  stat_type       TEXT NOT NULL,"
     "  pp_line         NUMERIC(8,2) NOT NULL,"
     "  sharp_line      NUMERIC(8,2),"
     "  sharp_over_prob NUMERIC(8,4),"
     "  best_side       TEXT,"
     "  best_prob       NUMERIC(8,4),"
     "  edge            NUMERIC(8,4),"
     "  game_id         INTEGER,"
     "  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
     "  PRIMARY KEY (run_date, player_name, stat_type, pp_line))"),
    # Sharp-market CLV: the sharp-implied prob for the pick's side captured near
    # game time (the close). picks.market_prob holds the same at pick-time, so
    # market_prob_close − market_prob is the closing-line-value edge against a
    # SHARP book (vs the existing, sticky PrizePicks line_close).
    ("0010_picks_market_close",
     "ALTER TABLE picks ADD COLUMN IF NOT EXISTS market_prob_close NUMERIC(8,4)"),
    # MLB ballpark weather per game (Open-Meteo) — drives hits/TB/HR offense.
    ("0011_game_weather",
     "CREATE TABLE IF NOT EXISTS game_weather ("
     "  game_id      INTEGER PRIMARY KEY,"
     "  temp_f       NUMERIC(6,1),"
     "  wind_mph     NUMERIC(6,1),"
     "  wind_dir     NUMERIC(6,1),"
     "  wind_out_mph NUMERIC(6,1),"
     "  humidity     NUMERIC(6,1),"
     "  is_dome      BOOLEAN,"
     "  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW())"),
    # Let backtest_runs also record model RETRAINS (regression MAE improvement),
    # so each retrain leaves a trail on the Performance tab's history chart.
    ("0012_backtest_runs_mae",
     "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS mae_improvement_pct DOUBLE PRECISION;"
     "ALTER TABLE backtest_runs ALTER COLUMN win_rate DROP NOT NULL;"
     "ALTER TABLE backtest_runs ALTER COLUMN sport DROP NOT NULL"),
    # Full scored prop universe (props.picks.score_universe). One row per
    # (game, player, stat, line) for EVERY player with a fresh PrizePicks
    # standard line on a modeled stat — not just the high-edge picks that get
    # logged. Powers the dashboard's "Build your own parlay" so it can offer any
    # player a model EV. model_prob is the SAME blended/dir-calibrated value
    # picks.model_prob stores, so the dashboard's calibrate() display stays
    # consistent across logged picks and unlogged props. Upserted daily.
    ("0013_scored_props",
     "CREATE TABLE IF NOT EXISTS scored_props ("
     "  id           BIGSERIAL PRIMARY KEY,"
     "  score_date   DATE NOT NULL,"
     "  sport_code   TEXT NOT NULL,"
     "  game_id      INTEGER NOT NULL,"
     "  player_id    INTEGER NOT NULL,"
     "  stat_type    TEXT NOT NULL,"
     "  line_value   NUMERIC(8,2) NOT NULL,"
     "  direction    TEXT,"
     "  model_prob   DOUBLE PRECISION,"
     "  edge         DOUBLE PRECISION,"
     "  ev           DOUBLE PRECISION,"
     "  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
     "  UNIQUE (game_id, player_id, stat_type, line_value));"
     "CREATE INDEX IF NOT EXISTS idx_scored_props_score_date"
     "  ON scored_props (score_date)"),
    # PrizePicks ships a per-player headshot URL; store it so the dashboard shows
    # real photos across all sports (WNBA especially — its players are pp_-keyed,
    # so the ESPN-id headshot URL the UI builds was invalid → fallback).
    ("0014_players_photo_url",
     "ALTER TABLE players ADD COLUMN IF NOT EXISTS photo_url TEXT"),
]


def run_migrations() -> int:
    """Apply any unapplied migrations. Returns the number applied. Safe to call
    on every startup — it's cheap and idempotent."""
    applied_count = 0
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  id text PRIMARY KEY, applied_at timestamptz DEFAULT now())"))
        applied = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}
        for mid, sql in MIGRATIONS:
            if mid in applied:
                continue
            for stmt in (s.strip() for s in sql.split(";")):
                if stmt:
                    conn.execute(text(stmt))
            conn.execute(text("INSERT INTO schema_migrations (id) VALUES (:i)"),
                         {"i": mid})
            log.info("migration_applied", id=mid)
            applied_count += 1
    return applied_count


if __name__ == "__main__":
    n = run_migrations()
    print(f"migrations applied: {n}")
