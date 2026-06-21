"""Ingest CBB boxscores into player_games via ESPN.

CBB schedule stores the ESPN event id directly as games.external_id, so no
nba_api->ESPN mapping is needed. Reuses the ESPN-basketball stat parser
(label-keyed, the same one NBA uses — avoids the positional-index trap).

Players are keyed on the UNIQUE ESPN athlete id (espn_<id>), not fuzzy name:
CBB has thousands of players and many shared names, so fuzzy matching would
mis-merge distinct people. There are no PrizePicks CBB lines until November, so
this backfill just builds the historical training base. NOVEMBER TODO: when CBB
lines arrive (pp_ keyed), verify the PrizePicks resolver fuzzy-matches onto these
espn_ rows (else add a reconciliation pass, as was needed for WNBA).
"""
import json
import time

from curl_cffi import requests as cc
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.ingest.nba_boxscores import parse_stats

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary"


def find_unprocessed_games(reprocess_all: bool = False) -> list[dict]:
    extra = "" if reprocess_all else \
        "AND NOT EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id)"
    with session_scope() as session:
        rows = session.execute(text(f"""
            SELECT g.game_id, g.external_id, g.game_date, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'cbb' AND g.external_id IS NOT NULL
              AND (g.status = 'final'
                   OR g.game_date >= (NOW() AT TIME ZONE 'America/Los_Angeles')::date - INTERVAL '5 days')
              {extra}
            ORDER BY g.game_date DESC
            LIMIT 400
        """)).all()
    return [{"game_id": r[0], "external_id": r[1], "game_date": r[2],
             "home_team_id": r[3], "away_team_id": r[4]} for r in rows]


def resolve_player(session, athlete_id: str, name: str, team_id: int) -> int:
    """Upsert keyed on the unique ESPN athlete id (collision-safe across the
    thousands of shared CBB names)."""
    res = session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name, current_team_id, active)
        VALUES ('cbb', :ext, :name, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = EXCLUDED.full_name, current_team_id = EXCLUDED.current_team_id
        RETURNING player_id
    """), {"ext": f"espn_{athlete_id}", "name": name, "tid": team_id}).first()
    return res[0]


def process_game(session, game: dict) -> int:
    event_id = game["external_id"]
    try:
        data = cc.get(ESPN_SUMMARY, params={"event": event_id},
                      impersonate="chrome120", timeout=15).json()
    except Exception as e:
        log.warning("cbb_boxscore_fetch_failed", event=event_id, err=str(e)[:120])
        return 0

    hdr = (data.get("header", {}).get("competitions") or [{}])[0]
    if not hdr.get("status", {}).get("type", {}).get("completed"):
        return 0
    session.execute(text("UPDATE games SET status='final' WHERE game_id=:gid AND status<>'final'"),
                    {"gid": game["game_id"]})

    home_away = {}
    for c in hdr.get("competitors", []):
        tid = str(c.get("id") or c.get("team", {}).get("id") or "")
        if tid:
            home_away[tid] = c.get("homeAway")

    rows = 0
    for team_entry in data.get("boxscore", {}).get("players", []):
        team_ext = str(team_entry.get("team", {}).get("id", ""))
        side = home_away.get(team_ext)
        if side == "home":
            team_id, opp_id, is_home = game["home_team_id"], game["away_team_id"], True
        elif side == "away":
            team_id, opp_id, is_home = game["away_team_id"], game["home_team_id"], False
        else:
            continue

        for stat_group in team_entry.get("statistics", []):
            keys = stat_group.get("keys", [])
            for athlete in stat_group.get("athletes", []):
                info = athlete.get("athlete", {})
                aid = info.get("id")
                name = info.get("displayName") or info.get("shortName")
                if not aid or not name:
                    continue
                stat_dict = parse_stats(keys, athlete.get("stats", []))
                mins = stat_dict["minutes"]
                player_id = resolve_player(session, aid, name, team_id)
                session.execute(text("""
                    INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                              is_home, did_play, minutes_played, stats, derived)
                    VALUES (:pid, :gid, :tid, :oid, :home, :played, :min,
                            CAST(:stats AS JSONB), '{}')
                    ON CONFLICT (player_id, game_id) DO UPDATE
                    SET stats = EXCLUDED.stats, minutes_played = EXCLUDED.minutes_played,
                        did_play = EXCLUDED.did_play
                """), {"pid": player_id, "gid": game["game_id"], "tid": team_id,
                       "oid": opp_id, "home": is_home, "played": mins > 0,
                       "min": round(mins, 2), "stats": json.dumps(stat_dict)})
                rows += 1
    return rows


def run(reprocess_all: bool = False):
    configure_logging()
    games = find_unprocessed_games(reprocess_all=reprocess_all)
    log.info("found_unprocessed_cbb_games", count=len(games))
    if not games:
        return
    total = failed = 0
    with session_scope() as session:
        for g in games:
            n = process_game(session, g)
            if n == 0:
                failed += 1
            total += n
            time.sleep(0.2)
    log.info("cbb_boxscore_ingest_complete", games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-process every final game")
    run(reprocess_all=ap.parse_args().all)
