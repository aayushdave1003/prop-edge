"""Sync the canonical 30 MLB teams (correct abbreviations) from the MLB Stats API.

The schedule ingest creates teams on the fly with `abbreviation = name[:3]`, which
collides the two LA / two NY / two Chicago / two SF-SD clubs (→ LOS/NEW/CHI/SAN)
and mangles St. Louis (→ "ST."), so 30 teams looked like 26. This pulls each
team's real abbreviation (LAD, LAA, NYY, NYM, CHC, CWS, SD, SF, STL, …) and
upserts by external_id, fixing existing rows in place. Idempotent; cheap; safe to
run daily.

Run:  python -m props.ingest.mlb_teams
"""
import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams?sportId=1&activeStatus=Y"


def run():
    configure_logging()
    resp = requests.get(TEAMS_URL, timeout=15)
    resp.raise_for_status()
    teams = resp.json().get("teams", [])
    inserted = updated = 0
    with session_scope() as s:
        for t in teams:
            eid = str(t.get("id"))
            abbr = t.get("abbreviation")
            name = t.get("name")
            if not (eid and abbr and name):
                continue
            res = s.execute(text("""
                INSERT INTO teams (sport_code, external_id, abbreviation, name)
                VALUES ('mlb', :eid, :abbr, :name)
                ON CONFLICT (sport_code, external_id) DO UPDATE
                    SET abbreviation = EXCLUDED.abbreviation, name = EXCLUDED.name
                RETURNING (xmax = 0) AS inserted
            """), {"eid": eid, "abbr": abbr, "name": name}).first()
            if res and res[0]:
                inserted += 1
            else:
                updated += 1
    log.info("mlb_teams_synced", fetched=len(teams), inserted=inserted, updated=updated)


if __name__ == "__main__":
    run()
