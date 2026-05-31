"""Pitcher starter confirmation — runs at 4pm and 7pm before games start.

For every unsettled MLB strikeouts_pitcher pick today:
  1. Fetch the current probable pitcher from the MLB Stats API
  2. Compare against the player logged in the pick
  3. If the probable pitcher changed (or the game has a bullpen day),
     void the pick so it doesn't sit unsettled forever

Also handles games that have already started: if the game is live/final
and the logged pitcher never appeared in the box score, void it.
"""
import requests
from datetime import date, timedelta
from sqlalchemy import text

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_LIVE_URL     = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"


def _fetch_probable_pitchers(game_date: date) -> dict:
    """Return {(home_team_ext_id, away_team_ext_id): (home_pitcher_ext_id, away_pitcher_ext_id)}."""
    r = requests.get(MLB_SCHEDULE_URL, params={
        "sportId": 1,
        "date": game_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher,team",
    }, timeout=15)
    r.raise_for_status()
    data = r.json()

    result = {}
    for block in data.get("dates", []):
        for g in block.get("games", []):
            home_ext = str(g["teams"]["home"]["team"]["id"])
            away_ext = str(g["teams"]["away"]["team"]["id"])
            home_pp  = g["teams"]["home"].get("probablePitcher")
            away_pp  = g["teams"]["away"].get("probablePitcher")
            status   = g["status"]["abstractGameState"]
            result[(home_ext, away_ext)] = {
                "home_pitcher_ext": str(home_pp["id"]) if home_pp else None,
                "away_pitcher_ext": str(away_pp["id"]) if away_pp else None,
                "game_pk":          str(g["gamePk"]),
                "status":           status,
            }
    return result


def _fetch_actual_starters(game_pk: str) -> set:
    """Return set of external player IDs who actually pitched (any innings)."""
    r = requests.get(MLB_LIVE_URL.format(pk=game_pk), timeout=15)
    if r.status_code != 200:
        return set()
    data = r.json()
    bs = data.get("liveData", {}).get("boxscore", {})
    starters = set()
    for side in ["home", "away"]:
        pitchers = bs.get("teams", {}).get(side, {}).get("pitchers", [])
        players  = bs.get("teams", {}).get(side, {}).get("players", {})
        for pid in pitchers:
            pdata = players.get(f"ID{pid}", {})
            ks = pdata.get("stats", {}).get("pitching", {}).get("strikeOuts")
            ip = pdata.get("stats", {}).get("pitching", {}).get("inningsPitched")
            # Only count pitchers who actually took the mound
            if ks is not None and ip not in (None, "0.0", "0"):
                starters.add(str(pid))
    return starters


def void_pick(session, pick_id: int, reason: str):
    session.execute(text("""
        UPDATE picks
        SET leg_result = 'void', settled_at = NOW()
        WHERE pick_id = :pid AND leg_result IS NULL
    """), {"pid": pick_id})
    log.info("pick_voided", pick_id=pick_id, reason=reason)


def run(target_date: date = None):
    if target_date is None:
        target_date = date.today()

    log.info("confirm_starters_start", date=target_date.isoformat())

    # Pull all unsettled pitcher picks for today
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pk.pick_id,
                   p.external_id  AS player_ext_id,
                   p.full_name,
                   ht.external_id AS home_team_ext,
                   at2.external_id AS away_team_ext,
                   g.game_date
            FROM picks pk
            JOIN players p   ON p.player_id   = pk.player_id
            JOIN games g     ON g.game_id      = pk.game_id
            JOIN teams ht    ON ht.team_id     = g.home_team_id
            JOIN teams at2   ON at2.team_id    = g.away_team_id
            WHERE pk.sport_code   = 'mlb'
              AND pk.stat_type    = 'strikeouts_pitcher'
              AND pk.leg_result   IS NULL
              AND g.game_date     = :d
        """), {"d": target_date}).fetchall()

    if not rows:
        log.info("no_pitcher_picks_to_confirm", date=target_date.isoformat())
        return

    log.info("pitcher_picks_found", n=len(rows))

    # Fetch current probable pitchers
    try:
        probables = _fetch_probable_pitchers(target_date)
    except Exception as e:
        log.warning("probable_pitcher_fetch_failed", error=str(e))
        return

    voided = confirmed = 0

    with session_scope() as session:
        for row in rows:
            pick_id       = row.pick_id
            player_ext    = row.player_ext_id
            home_team_ext = row.home_team_ext
            away_team_ext = row.away_team_ext
            player_name   = row.full_name

            game_info = probables.get((home_team_ext, away_team_ext))
            if not game_info:
                # Game not found in today's schedule — void
                void_pick(session, pick_id, "game_not_in_schedule")
                voided += 1
                continue

            status           = game_info["status"]
            home_pitcher_ext = game_info["home_pitcher_ext"]
            away_pitcher_ext = game_info["away_pitcher_ext"]
            game_pk          = game_info["game_pk"]

            if status in ("Live", "Final"):
                # Game started/finished — check if the player actually pitched
                try:
                    actual_starters = _fetch_actual_starters(game_pk)
                except Exception as e:
                    log.warning("actual_starter_fetch_failed", error=str(e), pk=game_pk)
                    continue

                if player_ext not in actual_starters:
                    void_pick(session, pick_id,
                              f"pitcher_did_not_start_pk={game_pk}")
                    voided += 1
                else:
                    confirmed += 1

            else:
                # Game hasn't started — check probable pitcher still matches
                probable_exts = {home_pitcher_ext, away_pitcher_ext} - {None}
                if player_ext not in probable_exts:
                    void_pick(session, pick_id,
                              f"probable_pitcher_changed_new={probable_exts}")
                    voided += 1
                else:
                    log.info("starter_confirmed", player=player_name, pick_id=pick_id)
                    confirmed += 1

    log.info("confirm_starters_done",
             confirmed=confirmed, voided=voided, total=len(rows))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    configure_logging()
    run(date.fromisoformat(args.date) if args.date else None)
