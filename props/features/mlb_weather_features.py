"""Inject ballpark weather into MLB player_games.derived for the offense models.

Reads the per-game `game_weather` table (populated by props.ingest.mlb_weather)
and writes two feature keys onto each MLB player-game:
  wx_temp      — first-pitch temperature (°F)
  wx_wind_out  — wind component blowing out to center field (mph; 0 for domes)

Run AFTER props.ingest.mlb_weather. Models pick these up once they're retrained
with the keys in FEATURE_KEYS (they default to 0 where weather is absent, so
adding the keys is safe before a full backfill).

Run:  python -m props.features.mlb_weather_features                 (all)
      python -m props.features.mlb_weather_features --since-days 5  (recent)
"""
import argparse

from sqlalchemy import text

from props.utils.db import session_scope
from props.features.derived_writer import write_derived
from props.utils.logging import log, configure_logging


def run(since_days: int = 99999):
    configure_logging()
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT pg.player_game_id, w.temp_f::float AS wtemp,
                   w.wind_out_mph::float AS wout
            FROM player_games pg
            JOIN games g        ON g.game_id = pg.game_id
            JOIN game_weather w ON w.game_id = pg.game_id
            WHERE g.sport_code = 'mlb'
              AND g.game_date >= (CURRENT_DATE - make_interval(days => :sd))
        """), {"sd": since_days}).all()
    fmap = {
        int(r.player_game_id): {
            "wx_temp": round(r.wtemp, 1) if r.wtemp is not None else 0,
            "wx_wind_out": round(r.wout, 1) if r.wout is not None else 0,
        }
        for r in rows
    }
    if fmap:
        write_derived(fmap.items(), mode="replace", label="mlb_weather")
    log.info("mlb_weather_features_done", player_games=len(fmap))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since-days", type=int, default=99999)
    args = p.parse_args()
    run(since_days=args.since_days)


if __name__ == "__main__":
    main()
