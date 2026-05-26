"""Ingest NBA boxscores for final games into player_games table.

For every NBA game marked final that doesn't yet have player_games rows,
fetches the boxscore via nba_api and writes one row per player.
"""
import time
import json
from datetime import datetime
from nba_api.stats.endpoints import boxscoretraditionalv3
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


def _to_int(v):
    try:
        return int(v) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _minutes_to_float(min_str) -> float:
    """Convert 'MM:SS' or 'PT30M5.000S' format to decimal minutes."""
    if not min_str:
        return 0.0
    s = str(min_str)
    if s.startswith("PT"):
        # ISO 8601 duration format: PT30M5.000S
        try:
            m_part = s.split("M")[0].replace("PT", "")
            s_part = s.split("M")[1].replace("S", "") if "M" in s and "S" in s else "0"
            return float(m_part) + float(s_part) / 60.0
        except (ValueError, IndexError):
            return 0.0
    if ":" in s:
        try:
            parts = s.split(":")
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def find_unprocessed_games() -> list[dict]:
    with session_scope() as session:
        rows = session.execute(text("""
            SELECT g.game_id, g.external_id,
                   g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'nba'
              AND g.status = 'final'
              AND NOT EXISTS (
                  SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id
              )
            ORDER BY g.game_date DESC
            LIMIT 100
        """)).all()
    return [
        {"game_id": r[0], "external_id": r[1],
         "home_team_id": r[2], "away_team_id": r[3]} for r in rows
    ]


def ensure_player(session, ext_id: str, full_name: str, position: str = None,
                  team_id: int = None) -> int:
    result = session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name,
                            position, current_team_id, active)
        VALUES ('nba', :ext, :name, :pos, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = EXCLUDED.full_name,
            current_team_id = EXCLUDED.current_team_id
        RETURNING player_id
    """), {"ext": ext_id, "name": full_name, "pos": position, "tid": team_id}).first()
    return result[0]


def process_game(session, game: dict, team_ext_to_id: dict):
    try:
        bs = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game["external_id"])
    except Exception as e:
        log.warning("boxscore_fetch_failed", game_ext=game["external_id"], err=str(e))
        return 0

    data = bs.get_dict()
    box = data.get("boxScoreTraditional", {})
    rows_inserted = 0

    for side_key, team_id_field in [("homeTeam", "home_team_id"),
                                     ("awayTeam", "away_team_id")]:
        team = box.get(side_key, {})
        opp_team_id = game["away_team_id"] if side_key == "homeTeam" else game["home_team_id"]
        team_id = game[team_id_field]

        for p in team.get("players", []):
            stats = p.get("statistics", {})
            ext_player_id = str(p.get("personId"))
            first = p.get("firstName") or ""
            family = p.get("familyName") or ""
            full_name = (first + " " + family).strip() or p.get("nameI") or f"NBA-{ext_player_id}"
            position = p.get("position") or None

            player_id = ensure_player(session, ext_player_id, full_name,
                                     position=position, team_id=team_id)

            stat_dict = {
                "minutes": _minutes_to_float(stats.get("minutes")),
                "points": _to_int(stats.get("points")),
                "rebounds": _to_int(stats.get("reboundsTotal")),
                "off_rebounds": _to_int(stats.get("reboundsOffensive")),
                "def_rebounds": _to_int(stats.get("reboundsDefensive")),
                "assists": _to_int(stats.get("assists")),
                "steals": _to_int(stats.get("steals")),
                "blocks": _to_int(stats.get("blocks")),
                "turnovers": _to_int(stats.get("turnovers")),
                "personal_fouls": _to_int(stats.get("foulsPersonal")),
                "fg_made": _to_int(stats.get("fieldGoalsMade")),
                "fg_attempted": _to_int(stats.get("fieldGoalsAttempted")),
                "fg3_made": _to_int(stats.get("threePointersMade")),
                "fg3_attempted": _to_int(stats.get("threePointersAttempted")),
                "ft_made": _to_int(stats.get("freeThrowsMade")),
                "ft_attempted": _to_int(stats.get("freeThrowsAttempted")),
                "plus_minus": _to_float(stats.get("plusMinusPoints")),
            }

            is_home = (side_key == "homeTeam")
            did_play = stat_dict["minutes"] > 0
            session.execute(text("""
                INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                          is_home, did_play, minutes_played,
                                          stats, derived)
                VALUES (:pid, :gid, :tid, :oid, :is_home, :did_play, :min, CAST(:stats AS JSONB), '{}')
                ON CONFLICT (player_id, game_id) DO NOTHING
            """), {
                "pid": player_id, "gid": game["game_id"],
                "tid": team_id, "oid": opp_team_id,
                "is_home": is_home, "did_play": did_play,
                "min": round(stat_dict["minutes"], 2),
                "stats": json.dumps(stat_dict),
            })
            rows_inserted += 1

    return rows_inserted


def run():
    configure_logging()
    games = find_unprocessed_games()
    log.info("found_unprocessed_games", count=len(games))
    if not games:
        return

    with session_scope() as session:
        team_rows = session.execute(text("""
            SELECT external_id, team_id FROM teams WHERE sport_code='nba'
        """)).all()
        team_ext_to_id = {row[0]: row[1] for row in team_rows}

        total_players = 0
        failed = 0
        for g in games:
            n = process_game(session, g, team_ext_to_id)
            if n == 0:
                failed += 1
            total_players += n
            time.sleep(0.6)  # rate limit politeness
        log.info("nba_boxscore_ingest_complete",
                 games=len(games), players=total_players, failed=failed)


if __name__ == "__main__":
    run()
