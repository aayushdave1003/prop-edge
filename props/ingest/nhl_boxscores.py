"""Ingest NHL boxscores for final games via NHL API."""
import json
import re
import time
import requests
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

NHL_BOXSCORE_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"
NHL_ROSTER_URL = "https://api-web.nhle.com/v1/roster/{abbr}/current"
HEADERS = {"User-Agent": "prop-edge/1.0"}

# The boxscore endpoint only ever returns an ABBREVIATED name ("B. Montour"),
# while every line feed uses the full one ("Brandon Montour"). Storing the
# abbreviated form divorced NHL box scores from NHL lines: 1,659 of 1,738 player
# rows were abbreviated and only 34 of 286 live Sleeper NHL names matched, so the
# sport produced no picks at all. The roster endpoint carries firstName/lastName
# keyed by the SAME player id we store as external_id, so it is the name source.
ABBREV_NAME = re.compile(r"^[A-Z]\.\s")


def fetch_full_names(session) -> dict[str, str]:
    """external_id -> "First Last" for every player on a current NHL roster.

    Best-effort: a team that fails to fetch simply contributes no names, and the
    caller falls back to the boxscore's abbreviated form. The `teams` table also
    holds national sides (Olympics / 4 Nations) which have no club roster — their
    404s are expected and not worth warning about.
    """
    abbrs = [r[0] for r in session.execute(text(
        "SELECT abbreviation FROM teams WHERE sport_code='nhl' AND abbreviation IS NOT NULL"))]
    names: dict[str, str] = {}
    for abbr in abbrs:
        try:
            r = requests.get(NHL_ROSTER_URL.format(abbr=abbr), headers=HEADERS, timeout=15)
            if not r.ok:
                continue
            data = r.json()
            for group in ("forwards", "defensemen", "goalies"):
                for p in data.get(group, []):
                    first = (p.get("firstName") or {}).get("default", "")
                    last = (p.get("lastName") or {}).get("default", "")
                    if first and last and p.get("id") is not None:
                        names[str(p["id"])] = f"{first} {last}"
        except Exception as e:
            log.warning("nhl_roster_fetch_failed", team=abbr, err=str(e)[:80])
    log.info("nhl_full_names_loaded", n=len(names))
    return names


def find_unprocessed_games() -> list[dict]:
    with session_scope() as session:
        rows = session.execute(text("""
            SELECT g.game_id, g.external_id, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'nhl'
              AND g.status = 'final'
              AND NOT EXISTS (
                  SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id
              )
            ORDER BY g.game_date DESC
            LIMIT 50
        """)).all()
    return [{"game_id": r[0], "external_id": r[1],
             "home_team_id": r[2], "away_team_id": r[3]} for r in rows]


def ensure_player(session, ext_id: str, full_name: str, team_id: int,
                  position: str = None) -> int:
    """Upsert a player, never DOWNGRADING a stored full name to an abbreviation.

    The old ON CONFLICT set full_name unconditionally, so even once a row held
    "Brandon Montour" the next boxscore run overwrote it with "B. Montour". The
    CASE keeps the incoming name only when it is not an abbreviation, or when
    what we already have is one.
    """
    result = session.execute(text(r"""
        INSERT INTO players (sport_code, external_id, full_name, position,
                            current_team_id, active)
        VALUES ('nhl', :ext, :name, :pos, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = CASE
                WHEN EXCLUDED.full_name ~ '^[A-Z]\.\s'
                     AND players.full_name !~ '^[A-Z]\.\s'
                THEN players.full_name
                ELSE EXCLUDED.full_name
            END,
            current_team_id = EXCLUDED.current_team_id
        RETURNING player_id
    """), {"ext": ext_id, "name": full_name, "pos": position, "tid": team_id}).first()
    return result[0]


def process_game(session, game: dict, tid_map: dict,
                 full_names: dict[str, str] | None = None) -> int:
    try:
        r = requests.get(NHL_BOXSCORE_URL.format(game_id=game["external_id"]),
                         headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("nhl_boxscore_fetch_failed", game_ext=game["external_id"], err=str(e))
        return 0

    rows = 0
    pbs = data.get("playerByGameStats", {})
    for side, team_id_field in [("homeTeam", "home_team_id"), ("awayTeam", "away_team_id")]:
        team_data = pbs.get(side, {})
        team_id   = game[team_id_field]
        opp_id    = game["away_team_id"] if side == "homeTeam" else game["home_team_id"]
        is_home   = (side == "homeTeam")

        # Skaters
        for p in team_data.get("forwards", []) + team_data.get("defense", []):
            ext_id = str(p.get("playerId", ""))
            # Prefer the roster's full name; the boxscore only has "B. Montour".
            name   = (full_names or {}).get(ext_id) or \
                     p.get("name", {}).get("default", f"NHL-{ext_id}")
            pos    = p.get("position")

            stat_dict = {
                "goals":            p.get("goals", 0) or 0,
                "assists":          p.get("assists", 0) or 0,
                "points":           p.get("points", 0) or 0,
                "shots":            p.get("sog", 0) or 0,
                "hits":             p.get("hits", 0) or 0,
                "blocked_shots":    p.get("blockedShots", 0) or 0,
                "penalty_minutes":  p.get("pim", 0) or 0,
                "plus_minus":       p.get("plusMinus", 0) or 0,
                "powerplay_goals":  p.get("powerPlayGoals", 0) or 0,
                "powerplay_points": p.get("powerPlayGoals", 0) or 0,
                "faceoff_wins":     p.get("faceoffWinningPctg", 0) or 0,
                "toi":              p.get("toi", "0:00"),
            }

            # Convert TOI "MM:SS" to decimal minutes
            toi_str = stat_dict.pop("toi", "0:00")
            try:
                parts = str(toi_str).split(":")
                mins = float(parts[0]) + float(parts[1]) / 60 if len(parts) == 2 else 0.0
            except (ValueError, IndexError):
                mins = 0.0
            stat_dict["minutes"] = round(mins, 2)

            pid = ensure_player(session, ext_id, name, team_id, pos)
            session.execute(text("""
                INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                          is_home, did_play, minutes_played, stats, derived)
                VALUES (:pid, :gid, :tid, :oid, :home, :played, :min,
                        CAST(:stats AS JSONB), '{}')
                ON CONFLICT (player_id, game_id) DO NOTHING
            """), {"pid": pid, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                   "home": is_home, "played": mins > 0, "min": mins,
                   "stats": json.dumps(stat_dict)})
            rows += 1

        # Goalies
        for p in team_data.get("goalies", []):
            ext_id = str(p.get("playerId", ""))
            name   = (full_names or {}).get(ext_id) or \
                     p.get("name", {}).get("default", f"NHL-G-{ext_id}")

            toi_str = p.get("toi", "0:00")
            try:
                parts = str(toi_str).split(":")
                mins = float(parts[0]) + float(parts[1]) / 60 if len(parts) == 2 else 0.0
            except (ValueError, IndexError):
                mins = 0.0

            raw_ssa = p.get("saveShotsAgainst", "0/0")
            try:
                saves_made = int(str(raw_ssa).split("/")[0])
                shots_faced = int(str(raw_ssa).split("/")[1])
            except (ValueError, IndexError, AttributeError):
                saves_made, shots_faced = 0, 0

            stat_dict = {
                "saves":           saves_made,
                "goals_against":   p.get("goalsAgainst", 0),
                "shots_against":   shots_faced,
                "save_pct":        p.get("savePctg", 0.0),
                "goals":           0, "assists": 0, "points": 0,
                "shots": 0, "hits": 0, "blocked_shots": 0,
                "minutes":         round(mins, 2),
            }
            pid = ensure_player(session, ext_id, name, team_id, "G")
            session.execute(text("""
                INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                          is_home, did_play, minutes_played, stats, derived)
                VALUES (:pid, :gid, :tid, :oid, :home, :played, :min,
                        CAST(:stats AS JSONB), '{}')
                ON CONFLICT (player_id, game_id) DO NOTHING
            """), {"pid": pid, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                   "home": is_home, "played": mins > 0, "min": mins,
                   "stats": json.dumps(stat_dict)})
            rows += 1

    return rows


def run():
    configure_logging()
    games = find_unprocessed_games()
    log.info("found_unprocessed_nhl_games", count=len(games))
    if not games:
        return

    with session_scope() as session:
        tid_rows = session.execute(text(
            "SELECT external_id, team_id FROM teams WHERE sport_code='nhl'"
        )).all()
        tid_map = {r[0]: r[1] for r in tid_rows}
        full_names = fetch_full_names(session)

    total = failed = 0
    with session_scope() as session:
        for g in games:
            n = process_game(session, g, tid_map, full_names)
            if n == 0: failed += 1
            total += n
            time.sleep(0.5)

    log.info("nhl_boxscore_ingest_complete", games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    run()
