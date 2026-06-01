"""Ingest WNBA schedule into the games table via ESPN API."""
import requests
from datetime import date, datetime
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"


def _normalize_status(detail: str) -> str:
    if not detail:
        return "scheduled"
    d = detail.lower()
    if "final" in d or "end" in d:
        return "final"
    if "postponed" in d or "canceled" in d:
        return "postponed"
    if any(q in d for q in ["1st", "2nd", "3rd", "4th", "half", "overtime", "ot"]):
        return "live"
    return "scheduled"


def fetch_wnba_games(target_date: date) -> list[dict]:
    r = requests.get(ESPN_SCOREBOARD,
                     params={"dates": target_date.strftime("%Y%m%d")},
                     timeout=15)
    r.raise_for_status()
    events = r.json().get("events", [])
    log.info("fetched_wnba_games", date=target_date.isoformat(), count=len(events))

    out = []
    for ev in events:
        comp = ev.get("competitions", [{}])[0]
        status_detail = comp.get("status", {}).get("type", {}).get("description", "")
        competitors = {c["homeAway"]: c for c in comp.get("competitors", [])}
        home = competitors.get("home", {})
        away = competitors.get("away", {})

        home_score = home.get("score")
        away_score = away.get("score")

        out.append({
            "external_id":    ev["id"],
            "game_datetime":  comp.get("startDate"),
            "status":         _normalize_status(status_detail),
            "home_team_ext":  home.get("team", {}).get("id"),
            "home_team_name": home.get("team", {}).get("displayName", ""),
            "home_team_abbr": home.get("team", {}).get("abbreviation", ""),
            "away_team_ext":  away.get("team", {}).get("id"),
            "away_team_name": away.get("team", {}).get("displayName", ""),
            "away_team_abbr": away.get("team", {}).get("abbreviation", ""),
            "home_score":     int(home_score) if home_score else None,
            "away_score":     int(away_score) if away_score else None,
        })
    return out


def upsert_games(games: list[dict], target_date: date):
    season = str(target_date.year)
    with session_scope() as session:
        # Upsert teams first
        for g in games:
            for side in [("home_team_ext", "home_team_name", "home_team_abbr"),
                         ("away_team_ext", "away_team_name", "away_team_abbr")]:
                ext, name, abbr = g[side[0]], g[side[1]], g[side[2]]
                if ext:
                    session.execute(text("""
                        INSERT INTO teams (sport_code, external_id, name, abbreviation)
                        VALUES ('wnba', :ext, :name, :abbr)
                        ON CONFLICT (sport_code, external_id) DO UPDATE
                        SET name = EXCLUDED.name
                    """), {"ext": ext, "name": name, "abbr": abbr[:5] if abbr else name[:5]})

        team_rows = session.execute(text(
            "SELECT external_id, team_id FROM teams WHERE sport_code='wnba'"
        )).all()
        tid_map = {r[0]: r[1] for r in team_rows}

        inserted = updated = 0
        for g in games:
            home_tid = tid_map.get(g["home_team_ext"])
            away_tid = tid_map.get(g["away_team_ext"])
            if not home_tid or not away_tid:
                continue
            result = session.execute(text("""
                INSERT INTO games (sport_code, external_id, game_date, game_datetime,
                                  season, season_type, home_team_id, away_team_id,
                                  home_score, away_score, status)
                VALUES ('wnba', :ext, :d, :dt, :season, 'regular',
                        :htid, :atid, :hs, :as_, :status)
                ON CONFLICT (sport_code, external_id) DO UPDATE
                SET status=EXCLUDED.status, home_score=EXCLUDED.home_score,
                    away_score=EXCLUDED.away_score
                RETURNING (xmax = 0) AS inserted
            """), {"ext": g["external_id"], "d": target_date, "dt": g["game_datetime"],
                   "season": season, "htid": home_tid, "atid": away_tid,
                   "hs": g["home_score"], "as_": g["away_score"], "status": g["status"]}).first()
            if result[0]: inserted += 1
            else: updated += 1

        log.info("wnba_schedule_ingest_complete", inserted=inserted, updated=updated)


def run(target_date: date = None):
    configure_logging()
    if target_date is None:
        target_date = date.today()
    log.info("fetching_wnba_schedule", date=target_date.isoformat())
    games = fetch_wnba_games(target_date)
    upsert_games(games, target_date)


if __name__ == "__main__":
    import sys
    run(datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else None)
