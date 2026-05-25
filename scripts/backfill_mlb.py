"""One-time backfill: pull MLB schedule + boxscores for 2023-2025 seasons."""
import argparse
from datetime import date, timedelta
from props.ingest.mlb_schedule import run as run_schedule
from props.ingest.mlb_boxscores import run as run_boxscores
from props.utils.logging import log, configure_logging

# MLB regular season roughly runs late March through early October
SEASONS = {
    2023: (date(2023, 3, 30), date(2023, 11, 1)),
    2024: (date(2024, 3, 28), date(2024, 11, 1)),
    2025: (date(2025, 3, 27), date(2025, 11, 1)),
}


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, choices=list(SEASONS.keys()),
                        help="If set, only do this season. Otherwise all.")
    parser.add_argument("--skip-boxscores", action="store_true",
                        help="Only pull schedule, not boxscores")
    args = parser.parse_args()

    configure_logging()
    seasons = [args.season] if args.season else list(SEASONS.keys())

    for season in seasons:
        start, end = SEASONS[season]
        log.info("season_start", season=season,
                 start=start.isoformat(), end=end.isoformat())
        days = list(daterange(start, end))
        for i, d in enumerate(days):
            try:
                run_schedule(d)
            except Exception as e:
                log.error("schedule_failed", date=d.isoformat(), error=str(e))
            if (i + 1) % 20 == 0:
                log.info("schedule_progress", season=season,
                         days_done=i + 1, total=len(days))
        log.info("season_schedule_complete", season=season)

    if not args.skip_boxscores:
        log.info("starting_boxscores_for_all_unprocessed_games")
        run_boxscores()


if __name__ == "__main__":
    main()
