"""Backfill NBA games + boxscores for a date range.

Usage:
    python scripts/backfill_nba.py 2024-10-22 2025-06-25
    python scripts/backfill_nba.py 2025-10-21 2026-05-26

Iterates one date at a time. For each date:
  1. Runs nba_schedule ingest
  2. Runs nba_boxscores ingest (only on games marked final)
  3. Logs progress

Resumable: rerunning the same date range only processes games not yet ingested.
"""
import sys
from datetime import date, datetime, timedelta
from props.ingest.nba_schedule import run as schedule_run
from props.ingest.nba_boxscores import run as boxscores_run
from props.utils.logging import configure_logging, log


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def main():
    configure_logging()
    if len(sys.argv) != 3:
        print("Usage: python scripts/backfill_nba.py YYYY-MM-DD YYYY-MM-DD")
        sys.exit(1)

    start = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    end = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
    total_days = (end - start).days + 1
    log.info("nba_backfill_start", start=str(start), end=str(end), days=total_days)

    for i, d in enumerate(daterange(start, end), 1):
        try:
            schedule_run(d)
        except Exception as e:
            log.warning("schedule_failed", date=str(d), err=str(e))
        try:
            boxscores_run()
        except Exception as e:
            log.warning("boxscores_failed", date=str(d), err=str(e))
        if i % 10 == 0:
            log.info("backfill_progress", days_done=i, days_total=total_days,
                     date=str(d))

    log.info("nba_backfill_complete")


if __name__ == "__main__":
    main()
