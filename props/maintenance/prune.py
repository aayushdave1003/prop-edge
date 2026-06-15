"""Retention prune — keeps the Railway volume from refilling (E2).

The 0.5 GB volume filled up and crashed prod once. Even on 5 GB, the
continuously-growing snapshot tables (`prop_lines`, `market_odds`) should be
bounded. This deletes rows past the retention window, while never touching a
`prop_lines` row that a logged `pick` still references (FK: picks.line_id).

Run: python -m props.maintenance.prune [--days 45] [--dry-run]
"""
import argparse

from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

DEFAULT_RETENTION_DAYS = 45


def prune(days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> dict:
    cutoff = f"NOW() - INTERVAL '{int(days)} days'"
    # (table, count-SQL, delete-SQL) — prop_lines excludes pick-referenced rows.
    plans = {
        "market_odds": (
            f"SELECT COUNT(*) FROM market_odds WHERE snapshot_time < {cutoff}",
            f"DELETE FROM market_odds WHERE snapshot_time < {cutoff}",
        ),
        "prop_lines": (
            f"SELECT COUNT(*) FROM prop_lines WHERE snapshot_at < {cutoff} "
            "AND line_id NOT IN (SELECT line_id FROM picks WHERE line_id IS NOT NULL)",
            f"DELETE FROM prop_lines WHERE snapshot_at < {cutoff} "
            "AND line_id NOT IN (SELECT line_id FROM picks WHERE line_id IS NOT NULL)",
        ),
        # Injury snapshots + ingest-run logs grow unbounded and nothing references
        # an old row — settle/display only use recent injuries.
        "player_injuries": (
            f"SELECT COUNT(*) FROM player_injuries WHERE fetched_at < {cutoff}",
            f"DELETE FROM player_injuries WHERE fetched_at < {cutoff}",
        ),
        "ingestion_runs": (
            f"SELECT COUNT(*) FROM ingestion_runs WHERE started_at < {cutoff}",
            f"DELETE FROM ingestion_runs WHERE started_at < {cutoff}",
        ),
    }
    result = {}
    with session_scope() as s:
        for table, (count_sql, delete_sql) in plans.items():
            n = s.execute(text(count_sql)).scalar() or 0
            if not dry_run and n:
                s.execute(text(delete_sql))
            result[table] = int(n)
            log.info("prune", table=table, rows=int(n),
                     days=days, applied=(not dry_run))
    log.info("prune_complete", deleted=result, dry_run=dry_run)
    return result


def main():
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    prune(days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
