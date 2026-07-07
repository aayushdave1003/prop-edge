"""Ingest WNBA boxscores for final games via ESPN API."""
import json
import time
import requests
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary"

# Fallback stat order if ESPN omits the `names` label array (it normally doesn't).
# This is ESPN's ACTUAL order — the parse maps by label, so order only matters here.
_WNBA_STAT_ORDER = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO",
                    "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def find_unprocessed_games(reprocess_all: bool = False) -> list[dict]:
    # reprocess_all=True re-fetches EVERY final game (used to correct historical
    # box scores after a parse fix); default only fills games with no rows yet.
    where_unprocessed = "" if reprocess_all else """
              AND NOT EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id)"""
    limit = "" if reprocess_all else "LIMIT 50"
    with session_scope() as session:
        rows = session.execute(text(f"""
            SELECT g.game_id, g.external_id, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.sport_code = 'wnba'
              AND g.status = 'final'
              AND g.external_id IS NOT NULL
              {where_unprocessed}
            ORDER BY g.game_date DESC
            {limit}
        """)).all()
    return [{"game_id": r[0], "external_id": r[1],
             "home_team_id": r[2], "away_team_id": r[3]} for r in rows]


def ensure_player(session, ext_id: str, full_name: str, team_id: int) -> int:
    """Resolve a WNBA box-score athlete to the SAME player row PrizePicks lines use.

    This previously blind-inserted keyed on the ESPN athlete id, which created a
    PARALLEL row for every player — divorcing box-score games (espn_ row) from
    PrizePicks lines (pp_ row). The split starved WNBA pick generation of
    features (lines on a row with 0 games) and left 91 duplicate players. Now we
    mirror nba_boxscores.resolve_player — fuzzy full-name match (pg_trgm) ->
    create — so games land on the row that already holds the lines/picks. The
    old last-name-only fallback was DROPPED: matching on surname alone could
    attach a box score to a different same-surname player, corrupting settlement
    (and the tracked win rate). Fail safe to create a new row instead.
    """
    res = session.execute(text("""
        SELECT player_id FROM players
        WHERE sport_code='wnba' AND similarity(full_name, :name) > 0.8
        ORDER BY similarity(full_name, :name) DESC LIMIT 1
    """), {"name": full_name}).first()
    if res:
        # Existing player (typically the pp_ lines row): attach games here and
        # keep the team current (the box score is authoritative for who suited up).
        session.execute(text("UPDATE players SET current_team_id=:tid, active=true "
                             "WHERE player_id=:pid"), {"tid": team_id, "pid": res[0]})
        return res[0]
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

                # Map by ESPN's label array, NOT hardcoded indices. ESPN's real
                # order is  MIN PTS FG 3PT FT REB AST TO STL BLK OREB DREB PF +/-
                # — the old hardcoded map assumed REB OREB DREB AST STL BLK TO,
                # so assists read STEALS, blocks read OREB, turnovers read DREB,
                # etc. (assists maxed at ~8, the steal count). Labels are robust
                # to ordering, the same approach nba_boxscores uses.
                labels = stat_group.get("names") or _WNBA_STAT_ORDER
                lidx = {lab: i for i, lab in enumerate(labels)}

                def _s(label, default=0):
                    i = lidx.get(label)
                    if i is None:
                        return default
                    try:
                        v = stats_list[i]
                        return 0 if v in ("--", "", None) else float(v)
                    except (IndexError, ValueError):
                        return default

                def _minutes(v):
                    try:
                        parts = str(v).split(":")
                        return float(parts[0]) + float(parts[1]) / 60 if len(parts) == 2 else float(v)
                    except (ValueError, IndexError):
                        return 0.0

                mins = _minutes(stats_list[lidx["MIN"]]) if stats_list and "MIN" in lidx else 0.0

                def _fg(label):
                    i = lidx.get(label)
                    if i is None:
                        return 0, 0
                    try:
                        made, att = str(stats_list[i]).split("-")
                        return int(made), int(att)
                    except Exception:
                        return 0, 0

                fg_made, fg_att   = _fg("FG")    # total FG (includes 3s)
                fg3_made, fg3_att = _fg("3PT")
                ft_made, ft_att   = _fg("FT")

                stat_dict = {
                    "minutes":        round(mins, 2),
                    "points":         int(_s("PTS")),
                    "rebounds":       int(_s("REB")),
                    "off_rebounds":   int(_s("OREB")),
                    "def_rebounds":   int(_s("DREB")),
                    "assists":        int(_s("AST")),
                    "steals":         int(_s("STL")),
                    "blocks":         int(_s("BLK")),
                    "turnovers":      int(_s("TO")),
                    "personal_fouls": int(_s("PF")),
                    "plus_minus":     _s("+/-"),
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
                    ON CONFLICT (player_id, game_id) DO UPDATE
                    SET stats = EXCLUDED.stats, minutes_played = EXCLUDED.minutes_played,
                        did_play = EXCLUDED.did_play
                """), {"pid": pid, "gid": game["game_id"], "tid": team_id, "oid": opp_id,
                       "home": is_home, "played": mins > 0, "min": round(mins, 2),
                       "stats": json.dumps(stat_dict)})
                rows += 1
    return rows


def run(reprocess_all: bool = False):
    configure_logging()
    games = find_unprocessed_games(reprocess_all=reprocess_all)
    log.info("found_unprocessed_wnba_games", count=len(games), reprocess_all=reprocess_all)
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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="re-fetch & correct EVERY final game (not just unprocessed)")
    run(reprocess_all=ap.parse_args().all)
