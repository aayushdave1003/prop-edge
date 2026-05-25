"""Pull MLB schedule into the games table."""
from datetime import date, datetime
import requests
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

def fetch_schedule(target_date: date) -> list[dict]:
    """MLB has a free public API. No auth needed."""
    params = {"sportId": 1, "date": target_date.strftime("%Y-%m-%d")}
    resp = requests.get(MLB_SCHEDULE_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    
    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            games.append({
                "external_id": str(g["gamePk"]),
                "game_datetime": g["gameDate"],
                "season": str(g["season"]),
                "season_type": "regular" if g["gameType"] == "R" else g["gameType"],
                "home_external_id": str(g["teams"]["home"]["team"]["id"]),
                "away_external_id": str(g["teams"]["away"]["team"]["id"]),
                "home_team_name": g["teams"]["home"]["team"]["name"],
                "away_team_name": g["teams"]["away"]["team"]["name"],
                "status": g["status"]["abstractGameState"].lower(),
            })
    return games

def upsert_team(session, external_id: str, name: str) -> int:
    """Insert team if new, return team_id."""
    result = session.execute(
        text("SELECT team_id FROM teams WHERE sport_code='mlb' AND external_id=:eid"),
        {"eid": external_id},
    ).first()
    if result:
        return result[0]
    
    result = session.execute(
        text("""
            INSERT INTO teams (sport_code, external_id, abbreviation, name)
            VALUES ('mlb', :eid, :abbr, :name)
            RETURNING team_id
        """),
        {"eid": external_id, "abbr": name[:3].upper(), "name": name},
    ).first()
    return result[0]

def upsert_game(session, game: dict) -> int:
    home_id = upsert_team(session, game["home_external_id"], game["home_team_name"])
    away_id = upsert_team(session, game["away_external_id"], game["away_team_name"])
    
    result = session.execute(
        text("""
            INSERT INTO games (sport_code, external_id, game_date, game_datetime,
                               season, season_type, home_team_id, away_team_id, status)
            VALUES ('mlb', :eid, :gdate, :gdt, :season, :stype, :home, :away, :status)
            ON CONFLICT (sport_code, external_id) DO UPDATE
                SET status = EXCLUDED.status,
                    game_datetime = EXCLUDED.game_datetime
            RETURNING game_id
        """),
        {
            "eid": game["external_id"],
            "gdate": datetime.fromisoformat(game["game_datetime"].replace("Z", "+00:00")).date(),
            "gdt": game["game_datetime"],
            "season": game["season"],
            "stype": game["season_type"],
            "home": home_id,
            "away": away_id,
            "status": game["status"],
        },
    ).first()
    return result[0]

def run(target_date: date | None = None):
    configure_logging()
    target_date = target_date or date.today()
    log.info("fetching_mlb_schedule", date=target_date.isoformat())
    
    games = fetch_schedule(target_date)
    log.info("fetched_games", count=len(games))
    
    with session_scope() as session:
        for g in games:
            upsert_game(session, g)
    
    log.info("ingestion_complete", games=len(games))

if __name__ == "__main__":
    run()
