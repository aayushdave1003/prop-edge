"""Ingest NBA schedule into the games table — via ESPN, with nba_api fallback.

ESPN became PRIMARY on 2026-11-09: stats.nba.com started hard-blocking the
GitHub runner's ScoreboardV3 calls on 2026-11-08 (every retry of every date in
that run timed out; the step is warn-only, so the failure was silent). With no
Nov-9 games rows, the morning line refresh skipped the entire ~960-line Sleeper
NBA slate as `unresolved_game` — a dark sport with zero errors raised. Box
scores made this exact move in July for the same reason; `nba_boxscores`
already resolves `espn_<event-id>` external ids natively, so ESPN-created rows
settle with no extra hop. nba_api remains the fallback for an ESPN hiccup
(it still works from residential IPs).

Rows are find-or-created by (game_date, home, away) — NOT by external_id — so a
game first seen through ESPN and later through nba_api (or vice versa) can
never fork into duplicate rows. (The 410-placeholder-game purge is the standing
lesson on letting near-duplicate game rows exist.)
"""
from datetime import date, datetime

from curl_cffi import requests as cc
from nba_api.stats.endpoints import scoreboardv3
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# ESPN uses slightly different NBA team abbreviations than we store (ours are
# nba_api's). Same map nba_boxscores and predict_today use.
ESPN_NBA_ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS",
                 "UTAH": "UTA", "WSH": "WAS"}

# ESPN season.type -> our season_type.
ESPN_SEASON_TYPE = {1: "preseason", 2: "regular", 3: "playoffs"}


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


def _espn_status(ev_status: dict) -> str:
    t = (ev_status or {}).get("type", {})
    if t.get("completed"):
        return "final"
    name = (t.get("name") or "").upper()
    if "POSTPONED" in name or "CANCELED" in name:
        return "postponed"
    if t.get("state") == "in":
        return "live"
    return "scheduled"


def _score(v):
    try:
        return int(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6), reraise=True)
def fetch_nba_games_espn(target_date: date) -> list[dict]:
    """Primary fetch: ESPN scoreboard (reachable from cloud runners)."""
    r = cc.get(ESPN_SCOREBOARD, params={"dates": target_date.strftime("%Y%m%d"), "limit": 50},
               impersonate="chrome120", timeout=20)
    r.raise_for_status()
    events = r.json().get("events", [])
    log.info("fetched_nba_games_espn", date=target_date.isoformat(), count=len(events))

    season, _ = _season_label(target_date)
    out = []
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors", [])
        h = next((c for c in cs if c.get("homeAway") == "home"), None)
        a = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not h or not a:
            continue
        ha = h.get("team", {}).get("abbreviation", "").upper()
        aa = a.get("team", {}).get("abbreviation", "").upper()
        out.append({
            "external_id": f"espn_{ev['id']}",
            "season": season,
            "season_type": ESPN_SEASON_TYPE.get((ev.get("season") or {}).get("type"), "unknown"),
            "home_team_ext": None,
            "away_team_ext": None,
            "home_team_abbr": ESPN_NBA_ABBR.get(ha, ha),
            "away_team_abbr": ESPN_NBA_ABBR.get(aa, aa),
            "home_score": _score(h.get("score")),
            "away_score": _score(a.get("score")),
            "status": _espn_status(comp.get("status") or ev.get("status")),
            "game_datetime": ev.get("date"),
        })
    return out


# stats.nba.com frequently read-times-out from cloud IPs (and hard-blocks the
# GitHub runner since 2026-11-08) — this is the FALLBACK path now. Retry with
# backoff but bound it: a 20s timeout still clears a healthy call (<5s), while
# a truly-down API fails in ~60s instead of ~140s.
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
            "home_team_abbr": None,
            "away_team_abbr": None,
            "home_score": home.get("score"),
            "away_score": away.get("score"),
            "status": _normalize_status(g.get("gameStatusText")),
            "game_datetime": g.get("gameTimeUTC"),
        })
    return out


def upsert_games(games: list[dict], target_date: date):
    with session_scope() as session:
        team_rows = session.execute(text("""
            SELECT external_id, abbreviation, team_id FROM teams WHERE sport_code='nba'
        """)).all()
        ext_map = {row[0]: row[2] for row in team_rows}
        abbr_map = {(row[1] or "").upper(): row[2] for row in team_rows}

        inserted = 0
        updated = 0
        missing_teams = []
        for g in games:
            home_tid = ext_map.get(g.get("home_team_ext")) or \
                abbr_map.get((g.get("home_team_abbr") or "").upper())
            away_tid = ext_map.get(g.get("away_team_ext")) or \
                abbr_map.get((g.get("away_team_abbr") or "").upper())
            if home_tid is None or away_tid is None:
                missing_teams.append((g.get("home_team_ext") or g.get("home_team_abbr"),
                                      g.get("away_team_ext") or g.get("away_team_abbr")))
                continue

            # Find-or-create by (date, teams): a game first seen via ESPN and
            # later via nba_api (or vice versa) must land on the SAME row —
            # external_id alone would fork a duplicate game per source.
            existing = session.execute(text("""
                SELECT game_id FROM games
                WHERE sport_code='nba' AND game_date=:d
                  AND home_team_id=:htid AND away_team_id=:atid
            """), {"d": target_date, "htid": home_tid, "atid": away_tid}).first()

            if existing:
                session.execute(text("""
                    UPDATE games
                    SET status = :status, home_score = :hs, away_score = :as_
                    WHERE game_id = :gid
                """), {"gid": existing[0], "status": g["status"],
                       "hs": g["home_score"], "as_": g["away_score"]})
                updated += 1
            else:
                session.execute(text("""
                    INSERT INTO games (sport_code, external_id, game_date, game_datetime,
                                      season, season_type, home_team_id, away_team_id,
                                      home_score, away_score, status)
                    VALUES ('nba', :ext, :d, :dt, :season, :stype,
                            :htid, :atid, :hs, :as_, :status)
                    ON CONFLICT (sport_code, external_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        home_score = EXCLUDED.home_score,
                        away_score = EXCLUDED.away_score
                """), {
                    "ext": g["external_id"], "d": target_date, "dt": g["game_datetime"],
                    "season": g["season"], "stype": g["season_type"],
                    "htid": home_tid, "atid": away_tid,
                    "hs": g["home_score"], "as_": g["away_score"],
                    "status": g["status"],
                })
                inserted += 1

        if missing_teams:
            log.warning("missing_teams", count=len(missing_teams), teams=missing_teams[:4])
        log.info("nba_schedule_ingest_complete",
                 inserted=inserted, updated=updated, missing=len(missing_teams))


def run(target_date: date = None):
    configure_logging()
    if target_date is None:
        target_date = date.today()
    log.info("fetching_nba_schedule", date=target_date.isoformat())
    try:
        games = fetch_nba_games_espn(target_date)
    except Exception as e:
        log.warning("espn_nba_schedule_failed_falling_back", err=str(e)[:120])
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
