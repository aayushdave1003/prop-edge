"""Scrape NBA injury statuses from ESPN's API and update player_games + a simple
"out tonight" filter that predict_today can use.

Runs daily as part of the morning ritual. Stores latest status per player in
a player_injuries table.
"""
from datetime import datetime
from curl_cffi import requests
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


URL = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def ensure_table():
    with session_scope() as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS player_injuries (
                player_name TEXT NOT NULL,
                team_name TEXT NOT NULL,
                status TEXT NOT NULL,
                short_comment TEXT,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (player_name, fetched_at)
            )
        """))
        session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_injuries_player_recent
            ON player_injuries (player_name, fetched_at DESC)
        """))


def fetch_injuries():
    r = requests.get(URL, impersonate="chrome120", timeout=20)
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
                    "status": status,
                    "short_comment": short,
                })
    return rows


def run():
    configure_logging()
    ensure_table()
    rows = fetch_injuries()
    log.info("fetched_injuries", n=len(rows))

    with session_scope() as session:
        for r in rows:
            session.execute(text("""
                INSERT INTO player_injuries (player_name, team_name, status, short_comment)
                VALUES (:name, :team, :status, :short)
            """), {"name": r["player_name"], "team": r["team_name"],
                   "status": r["status"], "short": r["short_comment"]})
    log.info("nba_injuries_stored", n=len(rows))

    # Report tonight's relevant impact
    out_count = sum(1 for r in rows if r["status"].lower() in ["out", "doubtful"])
    log.info("status_summary", out_or_doubtful=out_count, total=len(rows))


if __name__ == "__main__":
    run()
