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
