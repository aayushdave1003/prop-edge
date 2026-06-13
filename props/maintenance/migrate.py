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
