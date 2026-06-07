"""Ingest NBA boxscores for final games into player_games — via ESPN.

We deliberately use ESPN (site.api.espn.com) rather than nba_api/stats.nba.com:
stats.nba.com blocks datacenter IPs, so on GitHub Actions the old nba_api path
fetched nothing and NBA picks never settled. ESPN is reachable from cloud runners
(same source the NBA schedule fallback already uses).

Two ESPN-specific wrinkles this module handles:
  1. Stat columns are mapped by ESPN's `keys` array, NOT by position — the NBA
     column order differs from WNBA (REB precedes AST, OREB/DREB come later).
  2. Players are resolved by FUZZY NAME against existing rows (the same
     similarity>0.8 logic PrizePicks uses to attach lines to players). This is
     critical: settle_picks joins player_games to picks on player_id, so the
     box-score row must reuse the SAME player row the pick references. ESPN's
     athlete ids differ from nba_api's, so keying by external_id would create
     duplicate players and nothing would settle.
"""
import json
import time

from curl_cffi import requests as cc
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# ESPN abbreviation -> our (nba_api) abbreviation, for matching scoreboard
# events to games that carry a raw nba_api external_id.
ESPN_NBA_ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS",
                 "UTAH": "UTA", "WSH": "WAS"}


def _to_int(v):
    try:
        return int(float(v)) if v not in (None, "", "--") else 0
    except (ValueError, TypeError):
        return 0


def _to_float(v):
    try:
        return float(v) if v not in (None, "", "--") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _minutes(v) -> float:
    """ESPN reports minutes as a plain integer string ('31') but tolerate MM:SS."""
    s = str(v or "").strip()
    if ":" in s:
        try:
            mm, ss = s.split(":")[:2]
            return float(mm) + float(ss) / 60.0
        except ValueError:
            return 0.0
    return _to_float(s)


def _pair(v):
    """Split ESPN 'made-attempted' (e.g. '5-12') into (made, attempted)."""
    try:
        made, att = str(v).split("-")
        return int(made), int(att)
    except (ValueError, AttributeError):
        return 0, 0


def parse_stats(keys: list, stats_list: list) -> dict:
    """Map an ESPN athlete `stats` row to our stat dict via the `keys` array.

    Keyed by name, NOT position: the NBA column order differs from WNBA
    (REB precedes AST; OREB/DREB come after the shooting splits).
    """
    d = dict(zip(keys, stats_list)) if stats_list else {}
    fg_made, fg_att = _pair(d.get("fieldGoalsMade-fieldGoalsAttempted"))
    fg3_made, fg3_att = _pair(d.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
    ft_made, ft_att = _pair(d.get("freeThrowsMade-freeThrowsAttempted"))
    mins = _minutes(d.get("minutes"))
    return {
        "minutes": round(mins, 2),
        "points": _to_int(d.get("points")),
        "rebounds": _to_int(d.get("rebounds")),
        "off_rebounds": _to_int(d.get("offensiveRebounds")),
        "def_rebounds": _to_int(d.get("defensiveRebounds")),
        "assists": _to_int(d.get("assists")),
        "steals": _to_int(d.get("steals")),
        "blocks": _to_int(d.get("blocks")),
        "turnovers": _to_int(d.get("turnovers")),
        "personal_fouls": _to_int(d.get("fouls")),
        "fg_made": fg_made, "fg_attempted": fg_att,
        "fg3_made": fg3_made, "fg3_attempted": fg3_att,
        "threes_made": fg3_made, "threes_attempted": fg3_att,
        "ft_made": ft_made, "ft_attempted": ft_att,
        "plus_minus": _to_float(d.get("plusMinus")),
    }


def find_unprocessed_games() -> list[dict]:
    """NBA games lacking box scores: anything already final, plus recent games
    still marked live/scheduled — on datacenter the nba_api schedule update
    doesn't run, so games finish without our status ever flipping to 'final'.
    ESPN tells us the real status; process_game flips it when the game is done.
    """
    with session_scope() as session:
        rows = session.execute(text("""
            SELECT g.game_id, g.external_id, g.game_date,
                   g.home_team_id, g.away_team_id,
                   ht.abbreviation AS home_abbr, at.abbreviation AS away_abbr
            FROM games g
            LEFT JOIN teams ht ON ht.team_id = g.home_team_id
            LEFT JOIN teams at ON at.team_id = g.away_team_id
            WHERE g.sport_code = 'nba'
              AND NOT EXISTS (
                  SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id
              )
              AND (g.status = 'final'
                   OR g.game_date >= (NOW() AT TIME ZONE 'America/Los_Angeles')::date
                                     - INTERVAL '5 days')
            ORDER BY g.game_date DESC
            LIMIT 100
        """)).all()
    return [{"game_id": r[0], "external_id": r[1], "game_date": r[2],
             "home_team_id": r[3], "away_team_id": r[4],
             "home_abbr": r[5], "away_abbr": r[6]} for r in rows]


def resolve_player(session, name: str, team_id: int) -> int:
    """Find the existing NBA player row this name refers to, or create one.

    Mirrors PrizePicks' resolution order so a box-score row lands on the SAME
    player_id a pick used: fuzzy name match (pg_trgm) -> last-name match ->
    create with an espn_ external_id.
    """
    res = session.execute(text("""
        SELECT player_id FROM players
        WHERE sport_code = 'nba' AND similarity(full_name, :name) > 0.8
        ORDER BY similarity(full_name, :name) DESC
        LIMIT 1
    """), {"name": name}).first()
    if res:
        return res[0]

    last = name.rsplit(" ", 1)[-1]
    res = session.execute(text("""
        SELECT player_id FROM players
        WHERE sport_code = 'nba' AND full_name LIKE '%%' || :last
        LIMIT 1
    """), {"last": last}).first()
    if res:
        return res[0]

    res = session.execute(text("""
        INSERT INTO players (sport_code, external_id, full_name, current_team_id, active)
        VALUES ('nba', :ext, :name, :tid, true)
        ON CONFLICT (sport_code, external_id) DO UPDATE
        SET full_name = EXCLUDED.full_name
        RETURNING player_id
    """), {"ext": f"espn_{name.lower().replace(' ', '_')}", "name": name, "tid": team_id}).first()
    return res[0]


def _scoreboard_events(date_str: str) -> dict:
    """Map (home_abbr, away_abbr) in OUR abbreviations -> ESPN event id."""
    try:
        data = cc.get(ESPN_SCOREBOARD, params={"dates": date_str, "limit": 50},
                      impersonate="chrome120", timeout=15).json()
    except Exception as e:
        log.warning("espn_nba_scoreboard_failed", date=date_str, err=str(e)[:120])
        return {}
    out = {}
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors", [])
        h = next((c for c in cs if c.get("homeAway") == "home"), None)
        a = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not h or not a:
            continue
        ha = h.get("team", {}).get("abbreviation", "").upper()
        aa = a.get("team", {}).get("abbreviation", "").upper()
        key = (ESPN_NBA_ABBR.get(ha, ha), ESPN_NBA_ABBR.get(aa, aa))
        out[key] = ev["id"]
    return out


def _espn_event_id(game: dict, sb_cache: dict) -> str | None:
    ext = game.get("external_id") or ""
    if ext.startswith("espn_"):
        return ext.split("_", 1)[1]
    # Raw nba_api id — find the ESPN event by date + teams.
    date_str = game["game_date"].strftime("%Y%m%d")
    if date_str not in sb_cache:
        sb_cache[date_str] = _scoreboard_events(date_str)
    home = (game.get("home_abbr") or "").upper()
    away = (game.get("away_abbr") or "").upper()
    return sb_cache[date_str].get((home, away))


def process_game(session, game: dict, sb_cache: dict) -> int:
    event_id = _espn_event_id(game, sb_cache)
    if not event_id:
        log.warning("espn_nba_event_unresolved", game_id=game["game_id"],
                    ext=game.get("external_id"))
        return 0
    try:
        data = cc.get(ESPN_SUMMARY, params={"event": event_id},
                      impersonate="chrome120", timeout=15).json()
    except Exception as e:
        log.warning("nba_boxscore_fetch_failed", event=event_id, err=str(e)[:120])
        return 0

    hdr = (data.get("header", {}).get("competitions") or [{}])[0]
    # Only ingest finished games; ESPN is the source of truth for status, so
    # also flip our stale 'live'/'scheduled' rows to 'final' here (the nba_api
    # schedule updater can't run on datacenter).
    if not hdr.get("status", {}).get("type", {}).get("completed"):
        return 0
    session.execute(text(
        "UPDATE games SET status='final' WHERE game_id=:gid AND status<>'final'"),
        {"gid": game["game_id"]})

    # Map ESPN team id -> homeAway from the header so we can attach our team_ids
    # without relying on NBA team external_ids (which are nba_api, not ESPN).
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
                name = info.get("displayName") or info.get("shortName")
                if not name:
                    continue
                stat_dict = parse_stats(keys, athlete.get("stats", []))
                mins = stat_dict["minutes"]

                player_id = resolve_player(session, name, team_id)
                session.execute(text("""
                    INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                              is_home, did_play, minutes_played,
                                              stats, derived)
                    VALUES (:pid, :gid, :tid, :oid, :home, :played, :min,
                            CAST(:stats AS JSONB), '{}')
                    ON CONFLICT (player_id, game_id) DO NOTHING
                """), {"pid": player_id, "gid": game["game_id"], "tid": team_id,
                       "oid": opp_id, "home": is_home, "played": mins > 0,
                       "min": round(mins, 2), "stats": json.dumps(stat_dict)})
                rows += 1
    return rows


def run():
    configure_logging()
    games = find_unprocessed_games()
    log.info("found_unprocessed_games", count=len(games))
    if not games:
        return

    sb_cache: dict = {}
    total = failed = 0
    with session_scope() as session:
        for g in games:
            n = process_game(session, g, sb_cache)
            if n == 0:
                failed += 1
            total += n
            time.sleep(0.4)  # polite rate limit
    log.info("nba_boxscore_ingest_complete",
             games=len(games), players=total, failed=failed)


if __name__ == "__main__":
    run()
