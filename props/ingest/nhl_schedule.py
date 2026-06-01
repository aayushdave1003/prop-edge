"""Ingest NHL schedule into the games table via NHL API (api-web.nhle.com)."""
import requests
from datetime import date, datetime
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/{date}"
HEADERS = {"User-Agent": "prop-edge/1.0"}


def _normalize_status(state: str) -> str:
    state = (state or "").upper()
    if state in ("OFF", "FINAL", "7"):
        return "final"
    if state in ("LIVE", "3", "4", "5", "6"):
        return "live"
    if state in ("CRIT",):
        return "live"
    if state in ("PPD",):
        return "postponed"
    return "scheduled"


def fetch_nhl_games(target_date: date) -> list[dict]:
    url = NHL_SCHEDULE_URL.format(date=target_date.strftime("%Y-%m-%d"))
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()

    games = []
    for day in data.get("gameWeek", []):
        if day.get("date") != target_date.strftime("%Y-%m-%d"):
            continue
        for g in day.get("games", []):
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            games.append({
                "external_id":    str(g["id"]),
                "game_datetime":  g.get("startTimeUTC"),
                "status":         _normalize_status(str(g.get("gameState", ""))),
                "home_team_ext":  str(home.get("id", "")),
                "home_team_abbr": home.get("abbrev", ""),
                "home_team_name": home.get("placeName", {}).get("default", "") + " " +
                                  home.get("commonName", {}).get("default", ""),
                "away_team_ext":  str(away.get("id", "")),
                "away_team_abbr": away.get("abbrev", ""),
                "away_team_name": away.get("placeName", {}).get("default", "") + " " +
                                  away.get("commonName", {}).get("default", ""),
                "home_score":     home.get("score"),
                "away_score":     away.get("score"),
                "season":         str(g.get("season", target_date.year))[:4],
            })

    log.info("fetched_nhl_games", date=target_date.isoformat(), count=len(games))
    return games


def upsert_games(games: list[dict], target_date: date):
    with session_scope() as session:
        for g in games:
            for ext, name, abbr in [
                (g["home_team_ext"], g["home_team_name"].strip(), g["home_team_abbr"]),
                (g["away_team_ext"], g["away_team_name"].strip(), g["away_team_abbr"]),
            ]:
                if ext:
                    session.execute(text("""
                        INSERT INTO teams (sport_code, external_id, name, abbreviation)
                        VALUES ('nhl', :ext, :name, :abbr)
                        ON CONFLICT (sport_code, external_id) DO UPDATE SET name=EXCLUDED.name
                    """), {"ext": ext, "name": name or abbr, "abbr": abbr[:5] if abbr else "UNK"})

        team_rows = session.execute(text(
            "SELECT external_id, team_id FROM teams WHERE sport_code='nhl'"
        )).all()
        tid_map = {r[0]: r[1] for r in team_rows}

        inserted = updated = 0
        for g in games:
            htid = tid_map.get(g["home_team_ext"])
            atid = tid_map.get(g["away_team_ext"])
            if not htid or not atid:
                continue
            result = session.execute(text("""
                INSERT INTO games (sport_code, external_id, game_date, game_datetime,
                                  season, season_type, home_team_id, away_team_id,
                                  home_score, away_score, status)
                VALUES ('nhl', :ext, :d, :dt, :season, 'regular',
                        :htid, :atid, :hs, :as_, :status)
                ON CONFLICT (sport_code, external_id) DO UPDATE
                SET status=EXCLUDED.status, home_score=EXCLUDED.home_score,
                    away_score=EXCLUDED.away_score
                RETURNING (xmax = 0) AS inserted
            """), {"ext": g["external_id"], "d": target_date, "dt": g["game_datetime"],
                   "season": g["season"], "htid": htid, "atid": atid,
                   "hs": g["home_score"], "as_": g["away_score"], "status": g["status"]}).first()
            if result[0]: inserted += 1
            else: updated += 1

        log.info("nhl_schedule_ingest_complete", inserted=inserted, updated=updated)


def run(target_date: date = None):
    configure_logging()
    if target_date is None:
        target_date = date.today()
    log.info("fetching_nhl_schedule", date=target_date.isoformat())
    games = fetch_nhl_games(target_date)
    upsert_games(games, target_date)


if __name__ == "__main__":
    import sys
    run(datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else None)
