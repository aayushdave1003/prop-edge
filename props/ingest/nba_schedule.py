"""Ingest NBA schedule into the games table.

Pulls today's NBA games via nba_api ScoreboardV3, upserts to games table.
Run daily as part of the ritual.
"""
from datetime import date, datetime
from nba_api.stats.endpoints import scoreboardv3
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging


# Map NBA gameStatusText to our status field
STATUS_MAP = {
    "Final": "final",
    "Final/OT": "final",
    "Final/2OT": "final",
    "Final/3OT": "final",
    "Final/4OT": "final",
}


def _normalize_status(status_text: str) -> str:
    if not status_text:
        return "scheduled"
    if status_text.startswith("Final"):
        return "final"
    if "PPD" in status_text.upper() or "postpon" in status_text.lower():
        return "postponed"
    if "Q" in status_text or "OT" in status_text or "Half" in status_text:
        return "live"
    return "scheduled"


def _season_label(game_date: date) -> tuple[str, str]:
    """Return (season, season_type) for a game on this date.

    NBA seasons span Oct-Jun. We label by the season's *start* year, with
    season_type inferred from game_id prefix (regular vs playoffs).
    """
    year = game_date.year if game_date.month >= 10 else game_date.year - 1
    return str(year), "unknown"  # season_type filled per-game from game_id prefix


def _season_type_from_game_id(game_id: str) -> str:
    if not game_id:
        return "unknown"
    # NBA game ID format: SS-YY-NNN
    #   first 3 chars encode the type
    #   002 = regular, 004 = playoffs, 005 = play-in, 001 = preseason, 003 = all-star
    prefix = game_id[:3]
    return {
        "002": "regular",
        "004": "playoffs",
        "005": "play_in",
        "001": "preseason",
        "003": "all_star",
    }.get(prefix, "unknown")


# stats.nba.com frequently read-times-out from cloud IPs (the recurring NBA
# schedule failure in the daily logs). Retry with backoff so a transient stall
# doesn't drop the NBA slate — but bound it: a 20s timeout still clears a healthy
# call (those return in <5s) while a truly-DOWN API now fails in ~60s instead of
# ~140s, which kept the daily run from ballooning on a flaky-NBA-API day.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6), reraise=True)
def fetch_nba_games(target_date: date) -> list[dict]:
    sb = scoreboardv3.ScoreboardV3(game_date=target_date.strftime("%Y-%m-%d"), timeout=20)
    data = sb.get_dict()
    raw_games = data.get("scoreboard", {}).get("games", [])
    log.info("fetched_nba_games", date=target_date.isoformat(), count=len(raw_games))

    season, _ = _season_label(target_date)
    out = []
    for g in raw_games:
        gid = g.get("gameId", "")
        home = g.get("homeTeam", {})
        away = g.get("awayTeam", {})
        out.append({
            "external_id": gid,
            "season": season,
            "season_type": _season_type_from_game_id(gid),
            "home_team_ext": str(home.get("teamId")),
            "away_team_ext": str(away.get("teamId")),
            "home_score": home.get("score"),
            "away_score": away.get("score"),
            "status": _normalize_status(g.get("gameStatusText")),
            "game_datetime": g.get("gameTimeUTC"),
        })
    return out


def upsert_games(games: list[dict], target_date: date):
    with session_scope() as session:
        team_rows = session.execute(text("""
            SELECT external_id, team_id FROM teams WHERE sport_code='nba'
        """)).all()
        tid_map = {row[0]: row[1] for row in team_rows}

        inserted = 0
        updated = 0
        missing_teams = []
        for g in games:
            home_tid = tid_map.get(g["home_team_ext"])
            away_tid = tid_map.get(g["away_team_ext"])
            if home_tid is None or away_tid is None:
                missing_teams.append((g["home_team_ext"], g["away_team_ext"]))
                continue

            result = session.execute(text("""
                INSERT INTO games (sport_code, external_id, game_date, game_datetime,
                                  season, season_type, home_team_id, away_team_id,
                                  home_score, away_score, status)
                VALUES ('nba', :ext, :d, :dt, :season, :stype,
                        :htid, :atid, :hs, :as_, :status)
                ON CONFLICT (sport_code, external_id) DO UPDATE
                SET status = EXCLUDED.status,
                    home_score = EXCLUDED.home_score,
                    away_score = EXCLUDED.away_score
                RETURNING (xmax = 0) AS inserted
            """), {
                "ext": g["external_id"], "d": target_date, "dt": g["game_datetime"],
                "season": g["season"], "stype": g["season_type"],
                "htid": home_tid, "atid": away_tid,
                "hs": g["home_score"], "as_": g["away_score"],
                "status": g["status"],
            }).first()
            if result[0]:
                inserted += 1
            else:
                updated += 1

        if missing_teams:
            log.warning("missing_teams", count=len(missing_teams))
        log.info("nba_schedule_ingest_complete",
                 inserted=inserted, updated=updated, missing=len(missing_teams))


def run(target_date: date = None):
    configure_logging()
    if target_date is None:
        target_date = date.today()
    log.info("fetching_nba_schedule", date=target_date.isoformat())
    games = fetch_nba_games(target_date)
    upsert_games(games, target_date)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # Allow passing a date: python -m props.ingest.nba_schedule 2026-05-25
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        run(target)
    else:
        run()
