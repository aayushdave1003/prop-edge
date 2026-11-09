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


def find_unprocessed_games(reprocess_all: bool = False, limit: int = 600) -> list[dict]:
    extra = "" if reprocess_all else \
        "AND NOT EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id)"
    with session_scope() as session:
        rows = session.execute(text(f"""
            SELECT g.game_id, g.external_id, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'soccer' AND g.external_id IS NOT NULL AND g.status = 'final'
              {extra}
            ORDER BY g.game_date DESC LIMIT :lim
        """), {"lim": limit}).all()
    return [{"game_id": r[0], "external_id": r[1], "home_team_id": r[2], "away_team_id": r[3]} for r in rows]


def process_game(session, game: dict) -> int:
    try:
        data = cc.get(ESPN_SUMMARY, params={"event": game["external_id"]},
                      impersonate="chrome120", timeout=15).json()
    except Exception as e:
        log.warning("soccer_boxscore_fetch_failed", game_ext=game["external_id"], err=str(e)[:120])
        return 0
    rosters = data.get("rosters", [])
    if not rosters:
        return 0
    session.execute(text("UPDATE games SET status='final' WHERE game_id=:gid AND status<>'final'"),
                    {"gid": game["game_id"]})
    players: list[dict] = []
    pg_rows: list[dict] = []
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
            ext = f"espn_{aid}"
            players.append({"ext": ext, "tid": team_id,
                            "name": info.get("displayName") or info.get("shortName") or f"S-{aid}"})
            pg_rows.append({"ext": ext, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                            "home": is_home, "played": played, "stats": json.dumps(stats)})
    if not pg_rows:
        return 0
    # One pipelined upsert + one SELECT + one pipelined insert, instead of two
    # statements PER PLAYER: ~35 players/game over the Railway proxy was ~100
    # round-trips (~3s/game), which made the 2026-11-09 backfill crawl and look
    # hung between batch logs.
    session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name, current_team_id, active)
        VALUES ('soccer', :ext, :name, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = EXCLUDED.full_name, current_team_id = EXCLUDED.current_team_id
    """), players)
    pid_map = {r[0]: r[1] for r in session.execute(text("""
        SELECT external_id, player_id FROM players
        WHERE sport_code='soccer' AND external_id = ANY(:exts)
    """), {"exts": [p["ext"] for p in players]})}
    for r in pg_rows:
        r["pid"] = pid_map[r.pop("ext")]
    session.execute(text("""
        INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                  is_home, did_play, minutes_played, stats, derived)
        VALUES (:pid, :gid, :tid, :oid, :home, :played, 0, CAST(:stats AS JSONB), '{}')
        ON CONFLICT (player_id, game_id) DO UPDATE
        SET stats = EXCLUDED.stats, did_play = EXCLUDED.did_play
    """), pg_rows)
    return len(pg_rows)


def run(reprocess_all: bool = False, limit: int = 600, batch: int = 50):
    """Ingest box scores for unprocessed soccer games.

    Commits every `batch` games (same shape as cfb_boxscores, for the same
    reason): the whole loop used to run inside ONE session_scope, and the
    Railway proxy terminated that long-held connection mid-backfill
    (2026-11-09, ~600 games of ESPN round-trips) — rolling back every game the
    run had already fetched. Batching makes it restartable: re-running simply
    picks up whatever is still unprocessed.
    """
    configure_logging()
    games = find_unprocessed_games(reprocess_all=reprocess_all, limit=limit)
    log.info("found_unprocessed_soccer_games", count=len(games))
    if not games:
        return
    total = failed = 0
    for start in range(0, len(games), batch):
        chunk = games[start:start + batch]
        with session_scope() as session:
            for g in chunk:
                n = process_game(session, g)
                failed += (n == 0)
                total += n
                time.sleep(0.15)
        log.info("soccer_boxscore_batch", done=min(start + batch, len(games)),
                 of=len(games), players=total)
    log.info("soccer_boxscore_ingest_complete", games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=600, help="max games to consider")
    ap.add_argument("--batch", type=int, default=50, help="games per commit")
    a = ap.parse_args()
    run(reprocess_all=a.all, limit=a.limit, batch=a.batch)
