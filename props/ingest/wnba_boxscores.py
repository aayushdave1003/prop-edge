"""Ingest WNBA boxscores for final games via ESPN API."""
import json
import time
import requests
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary"


def find_unprocessed_games() -> list[dict]:
    with session_scope() as session:
        rows = session.execute(text("""
            SELECT g.game_id, g.external_id, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'wnba'
              AND g.status = 'final'
              AND NOT EXISTS (
                  SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id
              )
            ORDER BY g.game_date DESC
            LIMIT 50
        """)).all()
    return [{"game_id": r[0], "external_id": r[1],
             "home_team_id": r[2], "away_team_id": r[3]} for r in rows]


def ensure_player(session, ext_id: str, full_name: str, team_id: int) -> int:
    result = session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name, current_team_id, active)
        VALUES ('wnba', :ext, :name, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name=EXCLUDED.full_name, current_team_id=EXCLUDED.current_team_id
        RETURNING player_id
    """), {"ext": ext_id, "name": full_name, "tid": team_id}).first()
    return result[0]


def process_game(session, game: dict) -> int:
    try:
        r = requests.get(ESPN_SUMMARY, params={"event": game["external_id"]}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("wnba_boxscore_fetch_failed", game_ext=game["external_id"], err=str(e))
        return 0

    rows = 0
    boxscore = data.get("boxscore", {})
    players_by_team = boxscore.get("players", [])

    for team_entry in players_by_team:
        team_info = team_entry.get("team", {})
        team_ext  = str(team_info.get("id", ""))
        # Determine our team_id
        team_id = None
        with session_scope() as s2:
            res = s2.execute(text(
                "SELECT team_id FROM teams WHERE sport_code='wnba' AND external_id=:ext"
            ), {"ext": team_ext}).first()
            if res:
                team_id = res[0]
        if not team_id:
            continue

        opp_id = (game["away_team_id"] if team_id == game["home_team_id"]
                  else game["home_team_id"])
        is_home = (team_id == game["home_team_id"])

        for stat_group in team_entry.get("statistics", []):
            for athlete in stat_group.get("athletes", []):
                info   = athlete.get("athlete", {})
                ext_id = str(info.get("id", ""))
                name   = info.get("displayName", f"WNBA-{ext_id}")
                stats_list = athlete.get("stats", [])

                # ESPN stats order for WNBA: MIN PTS FG 3PT FT REB OREB DREB AST STL BLK TO PF +/-
                #                             0   1   2   3   4   5    6    7   8   9   10  11  12  13
                def _s(idx, default=0):
                    try:
                        v = stats_list[idx]
                        return 0 if v in ("--", "", None) else float(v)
                    except (IndexError, ValueError):
                        return default

                def _minutes(v):
                    try:
                        parts = str(v).split(":")
                        return float(parts[0]) + float(parts[1]) / 60 if len(parts) == 2 else float(v)
                    except (ValueError, IndexError):
                        return 0.0

                mins = _minutes(stats_list[0]) if stats_list else 0.0

                def _fg(idx):
                    try:
                        made, att = str(stats_list[idx]).split("-")
                        return int(made), int(att)
                    except Exception:
                        return 0, 0

                fg_made, fg_att   = _fg(2)   # total FG (includes 3s)
                fg3_made, fg3_att = _fg(3)   # 3PT
                ft_made, ft_att   = _fg(4)   # FT

                stat_dict = {
                    "minutes":        round(mins, 2),
                    "points":         int(_s(1)),
                    "rebounds":       int(_s(5)),
                    "off_rebounds":   int(_s(6)),
                    "def_rebounds":   int(_s(7)),
                    "assists":        int(_s(8)),
                    "steals":         int(_s(9)),
                    "blocks":         int(_s(10)),
                    "turnovers":      int(_s(11)),
                    "personal_fouls": int(_s(12)),
                    "plus_minus":     _s(13),
                    "fg_made":        fg_made,
                    "fg_attempted":   fg_att,
                    "threes_made":    fg3_made,
                    "threes_attempted": fg3_att,
                    "ft_made":        ft_made,
                    "ft_attempted":   ft_att,
                }

                pid = ensure_player(session, ext_id, name, team_id)
                session.execute(text("""
                    INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                              is_home, did_play, minutes_played,
                                              stats, derived)
                    VALUES (:pid, :gid, :tid, :oid, :home, :played, :min,
                            CAST(:stats AS JSONB), '{}')
                    ON CONFLICT (player_id, game_id) DO NOTHING
                """), {"pid": pid, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                       "home": is_home, "played": mins > 0, "min": round(mins, 2),
                       "stats": json.dumps(stat_dict)})
                rows += 1
    return rows


def run():
    configure_logging()
    games = find_unprocessed_games()
    log.info("found_unprocessed_wnba_games", count=len(games))
    if not games:
        return

    total = failed = 0
    with session_scope() as session:
        for g in games:
            n = process_game(session, g)
            if n == 0:
                failed += 1
            total += n
            time.sleep(0.5)

    log.info("wnba_boxscore_ingest_complete", games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    run()
