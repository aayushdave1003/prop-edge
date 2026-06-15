"""Sync the canonical 32 current NHL teams from the NHL API.

The schedule ingest only creates a team the first time it appears in a game, so
mid-/post-season our table held just the handful of teams that had played
recently (e.g. only 5 during the playoffs) — the lookup looked like the NHL has 5
teams. This seeds all 32 *active* clubs so the league is represented truthfully,
whether or not we have game data for them yet.

Active set comes from the standings (32 teams, triCode + full name); the numeric
team id (which matches the schedule's external_id, e.g. CAR=12, VGK=54) comes from
the stats endpoint. On a triCode collision (Utah Hockey Club → Utah Mammoth) we
take the newest franchise id. Idempotent; cheap; safe to run daily.

Run:  python -m props.ingest.nhl_teams
"""
import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

HEADERS = {"User-Agent": "prop-edge/1.0"}
STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"
STATS_TEAMS_URL = "https://api.nhle.com/stats/rest/en/team"


def _id_by_tricode() -> dict[str, int]:
    """triCode -> NHL team id (matches the schedule's external_id). The stats feed
    lists every franchise ever, so on a collision keep the newest (max) id."""
    data = requests.get(STATS_TEAMS_URL, headers=HEADERS, timeout=15).json()["data"]
    out: dict[str, int] = {}
    for t in data:
        tri = t.get("triCode")
        tid = t.get("id")
        if tri and tid is not None:
            out[tri] = max(out.get(tri, 0), int(tid))
    return out


def run():
    configure_logging()
    standings = requests.get(STANDINGS_URL, headers=HEADERS, timeout=15).json()["standings"]
    id_map = _id_by_tricode()
    inserted = updated = skipped = 0
    with session_scope() as s:
        for row in standings:
            abbr = row.get("teamAbbrev", {}).get("default")
            name = row.get("teamName", {}).get("default")
            eid = id_map.get(abbr)
            if not (abbr and name and eid):
                skipped += 1
                continue
            res = s.execute(text("""
                INSERT INTO teams (sport_code, external_id, abbreviation, name)
                VALUES ('nhl', :eid, :abbr, :name)
                ON CONFLICT (sport_code, external_id) DO UPDATE
                    SET abbreviation = EXCLUDED.abbreviation, name = EXCLUDED.name
                RETURNING (xmax = 0) AS inserted
            """), {"eid": str(eid), "abbr": abbr, "name": name}).first()
            if res and res[0]:
                inserted += 1
            else:
                updated += 1
    log.info("nhl_teams_synced", active=len(standings),
             inserted=inserted, updated=updated, skipped=skipped)


if __name__ == "__main__":
    run()
