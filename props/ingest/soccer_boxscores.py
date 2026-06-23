"""Ingest soccer boxscores via ESPN. Soccer is a different shape than the team
box scores: per-player stats live under summary['rosters'][team]['roster'][player]
['stats'] (a name/value list), not boxscore.players. ESPN provides shots, shots-
on-target, fouls, saves, goals, assists, cards — but NOT tackles/passes. Players
keyed on the unique ESPN athlete id.
"""
import json
import time

from curl_cffi import requests as cc
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary"

# ESPN roster-stat name -> our stat
STAT_MAP = {
    "totalShots": "shots", "shotsOnTarget": "shots_on_target",
    "foulsCommitted": "fouls", "foulsSuffered": "fouls_drawn",
    "saves": "saves", "totalGoals": "goals", "goalAssists": "assists",
    "yellowCards": "yellow_cards", "appearances": "appearances",
}


def _to_int(v) -> int:
    try:
        return int(float(v)) if v not in (None, "", "--") else 0
    except (ValueError, TypeError):
        return 0


def find_unprocessed_games(reprocess_all: bool = False) -> list[dict]:
    extra = "" if reprocess_all else \
        "AND NOT EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id)"
    with session_scope() as session:
        rows = session.execute(text(f"""
            SELECT g.game_id, g.external_id, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'soccer' AND g.external_id IS NOT NULL AND g.status = 'final'
              {extra}
            ORDER BY g.game_date DESC LIMIT 600
        """)).all()
    return [{"game_id": r[0], "external_id": r[1], "home_team_id": r[2], "away_team_id": r[3]} for r in rows]


def resolve_player(session, athlete_id: str, name: str, team_id: int) -> int:
    res = session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name, current_team_id, active)
        VALUES ('soccer', :ext, :name, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = EXCLUDED.full_name, current_team_id = EXCLUDED.current_team_id
        RETURNING player_id
    """), {"ext": f"espn_{athlete_id}", "name": name, "tid": team_id}).first()
    return res[0]


def process_game(session, game: dict) -> int:
    try:
        data = cc.get(ESPN_SUMMARY, params={"event": game["external_id"]},
                      impersonate="chrome120", timeout=15).json()
    except Exception as e:
        log.warning("soccer_boxscore_fetch_failed", event=game["external_id"], err=str(e)[:120])
        return 0
    rosters = data.get("rosters", [])
    if not rosters:
        return 0
    session.execute(text("UPDATE games SET status='final' WHERE game_id=:gid AND status<>'final'"),
                    {"gid": game["game_id"]})
    rows = 0
    for team in rosters:
        side = team.get("homeAway")
        if side == "home":
            team_id, opp_id, is_home = game["home_team_id"], game["away_team_id"], True
        elif side == "away":
            team_id, opp_id, is_home = game["away_team_id"], game["home_team_id"], False
        else:
            continue
        for p in team.get("roster", []):
            info = p.get("athlete", {})
            aid = info.get("id")
            if not aid:
                continue
            raw = {s.get("name"): s.get("value") for s in p.get("stats", [])}
            stats = {ours: _to_int(raw.get(espn)) for espn, ours in STAT_MAP.items()}
            played = stats.get("appearances", 0) > 0 or bool(p.get("starter"))
            pid = resolve_player(session, aid, info.get("displayName") or info.get("shortName") or f"S-{aid}", team_id)
            session.execute(text("""
                INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                          is_home, did_play, minutes_played, stats, derived)
                VALUES (:pid, :gid, :tid, :oid, :home, :played, 0, CAST(:stats AS JSONB), '{}')
                ON CONFLICT (player_id, game_id) DO UPDATE
                SET stats = EXCLUDED.stats, did_play = EXCLUDED.did_play
            """), {"pid": pid, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                   "home": is_home, "played": played, "stats": json.dumps(stats)})
            rows += 1
    return rows


def run(reprocess_all: bool = False):
    configure_logging()
    games = find_unprocessed_games(reprocess_all=reprocess_all)
    log.info("found_unprocessed_soccer_games", count=len(games))
    if not games:
        return
    total = failed = 0
    with session_scope() as session:
        for g in games:
            n = process_game(session, g)
            failed += (n == 0)
            total += n
            time.sleep(0.15)
    log.info("soccer_boxscore_ingest_complete", games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--all", action="store_true")
    run(reprocess_all=ap.parse_args().all)
