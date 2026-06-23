"""Ingest NFL boxscores into player_games via ESPN.

NFL box scores group stats by category (passing/rushing/receiving/…); a player
can appear in several (a QB passes + rushes), so we aggregate per athlete across
categories into one stat dict. Label-keyed (robust to column order). Players keyed
on the unique ESPN athlete id (NFL rosters are large; fuzzy name would mis-merge).
No PrizePicks NFL lines until the season — this backfill builds the training base.
"""
import json
import time

from curl_cffi import requests as cc
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"

# category -> [(ESPN label, our stat)]. "YDS"/"TD" repeat across categories, so
# they must be namespaced by category (passing_yards vs rushing_yards vs …).
CAT_MAP = {
    "passing":   [("YDS", "passing_yards"), ("TD", "passing_tds"), ("INT", "interceptions")],
    "rushing":   [("CAR", "carries"), ("YDS", "rushing_yards"), ("TD", "rushing_tds")],
    "receiving": [("REC", "receptions"), ("YDS", "receiving_yards"),
                  ("TD", "receiving_tds"), ("TGTS", "targets")],
}


def _to_int(v) -> int:
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "", "--") else 0
    except (ValueError, TypeError):
        return 0


def find_unprocessed_games(reprocess_all: bool = False) -> list[dict]:
    extra = "" if reprocess_all else \
        "AND NOT EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id)"
    with session_scope() as session:
        rows = session.execute(text(f"""
            SELECT g.game_id, g.external_id, g.game_date, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'nfl' AND g.external_id IS NOT NULL
              AND (g.status = 'final'
                   OR g.game_date >= (NOW() AT TIME ZONE 'America/Los_Angeles')::date - INTERVAL '7 days')
              {extra}
            ORDER BY g.game_date DESC LIMIT 400
        """)).all()
    return [{"game_id": r[0], "external_id": r[1], "game_date": r[2],
             "home_team_id": r[3], "away_team_id": r[4]} for r in rows]


def resolve_player(session, athlete_id: str, name: str, team_id: int) -> int:
    res = session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name, current_team_id, active)
        VALUES ('nfl', :ext, :name, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = EXCLUDED.full_name, current_team_id = EXCLUDED.current_team_id
        RETURNING player_id
    """), {"ext": f"espn_{athlete_id}", "name": name, "tid": team_id}).first()
    return res[0]


def parse_team_players(team_entry: dict) -> dict:
    """athlete_id -> {'name', 'stats'} aggregated across stat categories."""
    out: dict = {}
    for cat in team_entry.get("statistics", []):
        mapping = CAT_MAP.get(cat.get("name"))
        if not mapping:
            continue
        labels = cat.get("labels", [])
        for ath in cat.get("athletes", []):
            info = ath.get("athlete", {})
            aid = info.get("id")
            if not aid:
                continue
            d = dict(zip(labels, ath.get("stats", [])))
            rec = out.setdefault(str(aid), {"name": info.get("displayName"), "stats": {}})
            for lbl, stat in mapping:
                rec["stats"][stat] = _to_int(d.get(lbl))
            if cat["name"] == "passing" and "/" in str(d.get("C/ATT", "")):
                c, a = str(d["C/ATT"]).split("/")[:2]
                rec["stats"]["completions"], rec["stats"]["pass_attempts"] = _to_int(c), _to_int(a)
    return out


def process_game(session, game: dict) -> int:
    try:
        data = cc.get(ESPN_SUMMARY, params={"event": game["external_id"]},
                      impersonate="chrome120", timeout=15).json()
    except Exception as e:
        log.warning("nfl_boxscore_fetch_failed", event=game["external_id"], err=str(e)[:120])
        return 0
    hdr = (data.get("header", {}).get("competitions") or [{}])[0]
    if not hdr.get("status", {}).get("type", {}).get("completed"):
        return 0
    session.execute(text("UPDATE games SET status='final' WHERE game_id=:gid AND status<>'final'"),
                    {"gid": game["game_id"]})
    home_away = {str(c.get("id") or c.get("team", {}).get("id") or ""): c.get("homeAway")
                 for c in hdr.get("competitors", [])}

    rows = 0
    for team_entry in data.get("boxscore", {}).get("players", []):
        side = home_away.get(str(team_entry.get("team", {}).get("id", "")))
        if side == "home":
            team_id, opp_id, is_home = game["home_team_id"], game["away_team_id"], True
        elif side == "away":
            team_id, opp_id, is_home = game["away_team_id"], game["home_team_id"], False
        else:
            continue
        for aid, rec in parse_team_players(team_entry).items():
            pid = resolve_player(session, aid, rec["name"], team_id)
            session.execute(text("""
                INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                          is_home, did_play, minutes_played, stats, derived)
                VALUES (:pid, :gid, :tid, :oid, :home, true, 0, CAST(:stats AS JSONB), '{}')
                ON CONFLICT (player_id, game_id) DO UPDATE SET stats = EXCLUDED.stats
            """), {"pid": pid, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                   "home": is_home, "stats": json.dumps(rec["stats"])})
            rows += 1
    return rows


def run(reprocess_all: bool = False):
    configure_logging()
    games = find_unprocessed_games(reprocess_all=reprocess_all)
    log.info("found_unprocessed_nfl_games", count=len(games))
    if not games:
        return
    total = failed = 0
    with session_scope() as session:
        for g in games:
            n = process_game(session, g)
            failed += (n == 0)
            total += n
            time.sleep(0.2)
    log.info("nfl_boxscore_ingest_complete", games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    run(reprocess_all=ap.parse_args().all)
