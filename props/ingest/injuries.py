"""Scrape injury statuses from ESPN's API for NBA and MLB.

Stores latest status per player in player_injuries table. Runs daily.
"""
from curl_cffi import requests
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.maintenance.migrate import run_migrations


URLS = {
    "nba": "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
    "mlb": "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries",
}

# Statuses that mean "will not play tonight" — sport-specific
OUT_STATUSES = {
    "nba": {"Out", "Doubtful"},
    "mlb": {"10-Day-IL", "15-Day-IL", "60-Day-IL", "7-Day-IL", "Out"},
}


def fetch(sport: str):
    url = URLS[sport]
    r = requests.get(url, impersonate="chrome120", timeout=20)
    r.raise_for_status()
    data = r.json()
    rows = []
    for team in data.get("injuries", []):
        team_name = team.get("displayName", "")
        for inj in team.get("injuries", []):
            athlete = inj.get("athlete", {}).get("displayName", "")
            status = inj.get("status", "")
            short = inj.get("shortComment", "")[:300]
            if athlete and status:
                rows.append({
                    "player_name": athlete,
                    "team_name": team_name,
                    "sport_code": sport,
                    "status": status,
                    "short_comment": short,
                })
    return rows


def store(rows: list):
    with session_scope() as session:
        for r in rows:
            session.execute(text("""
                INSERT INTO player_injuries
                  (player_name, team_name, sport_code, status, short_comment)
                VALUES (:name, :team, :sport, :status, :short)
            """), {
                "name": r["player_name"], "team": r["team_name"],
                "sport": r["sport_code"], "status": r["status"],
                "short": r["short_comment"],
            })


def run():
    configure_logging()
    run_migrations()  # ensures player_injuries exists (migration 0004)
    for sport in URLS:
        rows = fetch(sport)
        store(rows)
        out_count = sum(1 for r in rows if r["status"] in OUT_STATUSES[sport])
        log.info("injuries_fetched", sport=sport, total=len(rows), out=out_count)


if __name__ == "__main__":
    run()
