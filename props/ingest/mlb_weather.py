"""MLB ballpark weather ingest (Open-Meteo — free, no API key).

Weather moves offense: warm air and wind blowing out carry fly balls (more hits,
total bases, home runs); cold/wind-in suppresses them; domes are neutral. This
fetches per-game conditions at first pitch and stores them in `game_weather`, to
(a) surface on the dashboard now and (b) feed the MLB models on the next retrain.

Park is keyed by the home team's MLB Stats API id (`teams.external_id`). For each
game we get temperature, wind speed/direction and humidity, and compute
`wind_out_mph` — the wind component blowing from home plate toward center field
(positive = out / offense-friendly), using each park's center-field bearing.

Run:  python -m props.ingest.mlb_weather              (today's games)
      python -m props.ingest.mlb_weather --since-days 30   (backfill recent)
"""
import argparse
import math
from datetime import date, datetime, timedelta

import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

# Home park per MLB Stats API team id: (lat, lon, center-field bearing°, dome).
# Bearings are approximate park orientations (home plate → CF); domes/closed
# roofs are weather-neutral so wind_out is forced to 0.
PARKS: dict[str, tuple] = {
    "108": (33.8003, -117.8827,  45, False),  # LAA  Angel Stadium
    "109": (33.4455, -112.0667,   0, True),   # ARI  Chase Field (retractable→treat closed)
    "110": (39.2839,  -76.6217,  32, False),  # BAL  Camden Yards
    "111": (42.3467,  -71.0972,  45, False),  # BOS  Fenway Park
    "112": (41.9484,  -87.6553,  30, False),  # CHC  Wrigley Field
    "113": (39.0975,  -84.5069,  30, False),  # CIN  Great American
    "114": (41.4962,  -81.6852,   0, False),  # CLE  Progressive Field
    "115": (39.7559, -104.9942,   0, False),  # COL  Coors Field (altitude!)
    "116": (42.3390,  -83.0485, 150, False),  # DET  Comerica Park
    "117": (29.7570,  -95.3551, 345, True),   # HOU  Daikin Park (retractable)
    "118": (39.0517,  -94.4803,  50, False),  # KC   Kauffman Stadium
    "119": (34.0739, -118.2400,  25, False),  # LAD  Dodger Stadium
    "120": (38.8730,  -77.0074,  30, False),  # WSH  Nationals Park
    "121": (40.7571,  -73.8458,  25, False),  # NYM  Citi Field
    "133": (38.5803, -121.5130,  60, False),  # ATH  Sutter Health Park (Sacramento)
    "134": (40.4469,  -80.0057, 120, False),  # PIT  PNC Park
    "135": (32.7073, -117.1566,   0, False),  # SD   Petco Park
    "136": (47.5914, -122.3325,  70, True),   # SEA  T-Mobile Park (retractable)
    "137": (37.7786, -122.3893,  85, False),  # SF   Oracle Park (bay wind)
    "138": (38.6226,  -90.1928,  70, False),  # STL  Busch Stadium
    "139": (27.7682,  -82.6534,   0, True),   # TB   Tropicana Field (dome)
    "140": (32.7473,  -97.0847,   0, True),   # TEX  Globe Life Field (retractable)
    "141": (43.6414,  -79.3894,   0, True),   # TOR  Rogers Centre (retractable)
    "142": (44.9817,  -93.2776,  80, False),  # MIN  Target Field
    "143": (39.9061,  -75.1665,  15, False),  # PHI  Citizens Bank Park
    "144": (33.8908,  -84.4678,  25, False),  # ATL  Truist Park
    "145": (41.8300,  -87.6338, 130, False),  # CWS  Rate Field
    "146": (25.7781,  -80.2197,  30, True),   # MIA  loanDepot park (retractable)
    "147": (40.8296,  -73.9262,   0, False),  # NYY  Yankee Stadium
    "158": (43.0280,  -87.9712,   0, True),   # MIL  American Family Field (retractable)
}

# Forecast serves today + the near future + a few days of recent past; older
# dates need the historical reanalysis (archive) endpoint.
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def wind_out_mph(speed_mph: float, from_dir_deg: float, cf_bearing: float) -> float:
    """Component of wind blowing from home plate toward center field (out)."""
    blowing_to = (from_dir_deg + 180) % 360          # met. dir is where wind is FROM
    return round(speed_mph * math.cos(math.radians(blowing_to - cf_bearing)), 1)


def _fetch(lat: float, lon: float, game_date: date, hour_local: int = 19) -> dict | None:
    """Open-Meteo hourly conditions near first pitch (default 7 PM local).
    Uses the archive (reanalysis) endpoint for older dates, forecast otherwise."""
    url = ARCHIVE_URL if game_date < date.today() - timedelta(days=3) else FORECAST_URL
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "auto", "start_date": game_date.isoformat(),
        "end_date": game_date.isoformat(),
    }
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            h = r.json().get("hourly", {})
            times = h.get("time", [])
            if not times or h.get("temperature_2m", [None])[0] is None:
                return None
            idx = min(range(len(times)),
                      key=lambda i: abs(int(times[i][11:13]) - hour_local))
            return {
                "temp_f": h["temperature_2m"][idx],
                "wind_mph": h["wind_speed_10m"][idx],
                "wind_dir": h["wind_direction_10m"][idx],
                "humidity": h["relative_humidity_2m"][idx],
            }
        except Exception as e:
            if attempt == 1:
                log.warning("weather_fetch_failed", error=str(e)[:120])
    return None


def run(since_days: int = 0):
    configure_logging()
    today = date.today()
    since = today - timedelta(days=since_days)
    with session_scope() as s:
        games = s.execute(text("""
            SELECT g.game_id, t.external_id AS home_ext, g.game_date, g.game_datetime
            FROM games g
            JOIN teams t ON t.team_id = g.home_team_id
            WHERE g.sport_code = 'mlb'
              AND g.game_date BETWEEN :since AND :tomorrow
              AND NOT EXISTS (SELECT 1 FROM game_weather w WHERE w.game_id = g.game_id)
            ORDER BY g.game_date
        """), {"since": since, "tomorrow": today + timedelta(days=1)}).all()

        wrote = 0
        for g in games:
            park = PARKS.get(str(g.home_ext))
            if not park:
                continue
            lat, lon, cf_bearing, dome = park
            hour = g.game_datetime.hour if g.game_datetime else 19
            wx = _fetch(lat, lon, g.game_date, hour)
            if not wx:
                continue
            wout = 0.0 if dome else wind_out_mph(wx["wind_mph"], wx["wind_dir"], cf_bearing)
            s.execute(text("""
                INSERT INTO game_weather (game_id, temp_f, wind_mph, wind_dir,
                                          wind_out_mph, humidity, is_dome, fetched_at)
                VALUES (:gid, :t, :w, :wd, :wo, :h, :dome, NOW())
                ON CONFLICT (game_id) DO UPDATE SET
                    temp_f=EXCLUDED.temp_f, wind_mph=EXCLUDED.wind_mph,
                    wind_dir=EXCLUDED.wind_dir, wind_out_mph=EXCLUDED.wind_out_mph,
                    humidity=EXCLUDED.humidity, is_dome=EXCLUDED.is_dome, fetched_at=NOW()
            """), {"gid": g.game_id, "t": wx["temp_f"], "w": wx["wind_mph"],
                   "wd": wx["wind_dir"], "wo": wout, "h": wx["humidity"], "dome": dome})
            wrote += 1
    log.info("mlb_weather_done", games=len(games), wrote=wrote)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since-days", type=int, default=0,
                   help="also (back)fill games this many days back")
    args = p.parse_args()
    run(since_days=args.since_days)


if __name__ == "__main__":
    main()
