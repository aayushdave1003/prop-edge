"""Ingest soccer (club) schedule via ESPN. Starts with the EPL (eng.1) — the
biggest, most-priced league; more leagues can be added to LEAGUES later.
sport_code='soccer'. Each ESPN event is one match (home vs away)."""
import requests
from datetime import date, datetime
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

LEAGUES = ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "usa.1"]   # EPL, La Liga, Bundesliga, Serie A, Ligue 1, MLS
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard"


def _status(detail: str) -> str:
    d = (detail or "").lower()
    if "ft" in d or "full" in d or "final" in d:
        return "final"
    if "postponed" in d or "canceled" in d:
        return "postponed"
    if "'" in d or "half" in d or "ht" in d:
        return "live"
    return "scheduled"


def fetch_games(target_date: date, league: str) -> list[dict]:
    r = requests.get(ESPN.format(lg=league),
                     params={"dates": target_date.strftime("%Y%m%d")}, timeout=20)
    r.raise_for_status()
    events = r.json().get("events", [])
    out = []
    for ev in events:
        comp = ev.get("competitions", [{}])[0]
        sd = comp.get("status", {}).get("type", {}).get("description", "")
        cc = {c["homeAway"]: c for c in comp.get("competitors", [])}
        home, away = cc.get("home", {}), cc.get("away", {})
        hs, as_ = home.get("score"), away.get("score")
        out.append({"external_id": ev["id"], "game_datetime": comp.get("startDate"),
                    "status": _status(sd),
                    "home_team_ext": home.get("team", {}).get("id"),
                    "home_team_name": home.get("team", {}).get("displayName", ""),
                    "home_team_abbr": home.get("team", {}).get("abbreviation", ""),
                    "away_team_ext": away.get("team", {}).get("id"),
                    "away_team_name": away.get("team", {}).get("displayName", ""),
                    "away_team_abbr": away.get("team", {}).get("abbreviation", ""),
                    "home_score": int(hs) if hs not in (None, "") else None,
                    "away_score": int(as_) if as_ not in (None, "") else None})
    return out


def upsert_games(games: list[dict], target_date: date):
    season = str(target_date.year if target_date.month >= 7 else target_date.year - 1)
    with session_scope() as session:
        for g in games:
            for ext, name, abbr in [(g["home_team_ext"], g["home_team_name"], g["home_team_abbr"]),
                                    (g["away_team_ext"], g["away_team_name"], g["away_team_abbr"])]:
                if ext:
                    session.execute(text("""
                        INSERT INTO teams (sport_code, external_id, name, abbreviation)
                        VALUES ('soccer', :ext, :name, :abbr)
                        ON CONFLICT (sport_code, external_id) DO UPDATE SET name = EXCLUDED.name
                    """), {"ext": ext, "name": name, "abbr": abbr[:5] if abbr else name[:5]})
        tid = {r[0]: r[1] for r in session.execute(text(
            "SELECT external_id, team_id FROM teams WHERE sport_code='soccer'")).all()}
        inserted = updated = 0
        for g in games:
            h, a = tid.get(g["home_team_ext"]), tid.get(g["away_team_ext"])
            if not h or not a:
                continue
            res = session.execute(text("""
                INSERT INTO games (sport_code, external_id, game_date, game_datetime, season,
                                  season_type, home_team_id, away_team_id, home_score, away_score, status)
                VALUES ('soccer', :ext, :d, :dt, :season, 'regular', :h, :a, :hs, :as_, :status)
                ON CONFLICT (sport_code, external_id) DO UPDATE
                SET status=EXCLUDED.status, home_score=EXCLUDED.home_score, away_score=EXCLUDED.away_score
                RETURNING (xmax = 0) AS inserted
            """), {"ext": g["external_id"], "d": target_date, "dt": g["game_datetime"], "season": season,
                   "h": h, "a": a, "hs": g["home_score"], "as_": g["away_score"], "status": g["status"]}).first()
            inserted += bool(res[0]); updated += (not res[0])
        log.info("soccer_schedule_ingest_complete", inserted=inserted, updated=updated)


def run(target_date: date = None):
    configure_logging()
    if target_date is None:
        target_date = date.today()
    for lg in LEAGUES:
        games = fetch_games(target_date, lg)
        log.info("fetched_soccer_games", date=target_date.isoformat(), league=lg, count=len(games))
        upsert_games(games, target_date)


if __name__ == "__main__":
    import sys
    run(datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else None)
