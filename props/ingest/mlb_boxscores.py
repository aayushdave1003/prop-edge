"""Pull MLB box scores for completed games and populate players + player_games."""
import json
from datetime import datetime
import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def fetch_boxscore(game_pk):
    resp = requests.get(BOXSCORE_URL.format(game_pk=game_pk), timeout=15)
    resp.raise_for_status()
    return resp.json()


def upsert_player(session, external_id, full_name, position, team_id):
    result = session.execute(
        text("SELECT player_id FROM players WHERE sport_code='mlb' AND external_id=:eid"),
        {"eid": external_id},
    ).first()
    if result:
        session.execute(
            text("UPDATE players SET current_team_id=:tid WHERE player_id=:pid"),
            {"tid": team_id, "pid": result[0]},
        )
        return result[0]
    result = session.execute(
        text("""
            INSERT INTO players (sport_code, external_id, full_name, position, current_team_id)
            VALUES ('mlb', :eid, :name, :pos, :tid)
            RETURNING player_id
        """),
        {"eid": external_id, "name": full_name, "pos": position, "tid": team_id},
    ).first()
    return result[0]


def extract_batting_stats(stats):
    b = stats.get("batting", {})
    return {
        "at_bats": b.get("atBats", 0),
        "runs": b.get("runs", 0),
        "hits": b.get("hits", 0),
        "doubles": b.get("doubles", 0),
        "triples": b.get("triples", 0),
        "home_runs": b.get("homeRuns", 0),
        "rbis": b.get("rbi", 0),
        "walks": b.get("baseOnBalls", 0),
        "strikeouts": b.get("strikeOuts", 0),
        "stolen_bases": b.get("stolenBases", 0),
        "total_bases": b.get("totalBases", 0),
        "plate_appearances": b.get("plateAppearances", 0),
        "left_on_base": b.get("leftOnBase", 0),
    }


def extract_pitching_stats(stats):
    p = stats.get("pitching", {})
    ip_str = p.get("inningsPitched", "0.0")
    try:
        whole, frac = ip_str.split(".")
        outs = int(whole) * 3 + int(frac)
    except (ValueError, AttributeError):
        outs = 0
    return {
        "outs_recorded": outs,
        "hits_allowed": p.get("hits", 0),
        "runs_allowed": p.get("runs", 0),
        "earned_runs": p.get("earnedRuns", 0),
        "walks_allowed": p.get("baseOnBalls", 0),
        "strikeouts_pitcher": p.get("strikeOuts", 0),
        "home_runs_allowed": p.get("homeRuns", 0),
        "pitches_thrown": p.get("numberOfPitches", 0),
        "batters_faced": p.get("battersFaced", 0),
    }


def process_side(session, boxscore, side, game_id, team_id, opponent_id, is_home):
    players_data = boxscore["teams"][side]["players"]
    inserted = 0
    for player_key, pdata in players_data.items():
        person = pdata.get("person", {})
        external_id = str(person.get("id"))
        full_name = person.get("fullName", "")
        position = (pdata.get("position") or {}).get("abbreviation")
        stats = pdata.get("stats", {})
        batting = extract_batting_stats(stats)
        pitching = extract_pitching_stats(stats)
        if batting["plate_appearances"] == 0 and pitching["batters_faced"] == 0:
            continue
        player_id = upsert_player(session, external_id, full_name, position, team_id)
        combined_stats = {**batting, **pitching}
        session.execute(
            text("""
                INSERT INTO player_games (player_id, game_id, team_id, opponent_id,
                                          is_home, did_play, stats)
                VALUES (:pid, :gid, :tid, :oid, :home, TRUE, CAST(:stats AS JSONB))
                ON CONFLICT (player_id, game_id) DO UPDATE
                    SET stats = EXCLUDED.stats, updated_at = NOW()
            """),
            {
                "pid": player_id, "gid": game_id, "tid": team_id, "oid": opponent_id,
                "home": is_home, "stats": json.dumps(combined_stats),
            },
        )
        inserted += 1
    return inserted


def get_unprocessed_games(session):
    rows = session.execute(text("""
        SELECT g.game_id, g.external_id, g.home_team_id, g.away_team_id
        FROM games g
        WHERE g.sport_code = 'mlb' AND g.status = 'final'
          AND NOT EXISTS (
              SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id
          )
        ORDER BY g.game_date DESC
    """)).fetchall()
    return [{"game_id": r[0], "external_id": r[1],
             "home_team_id": r[2], "away_team_id": r[3]} for r in rows]


def run(limit=None):
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('mlb_boxscores', :s, 'running')
            RETURNING run_id
        """), {"s": started}).scalar()
    with session_scope() as session:
        games = get_unprocessed_games(session)
    log.info("found_unprocessed_games", count=len(games))
    if limit:
        games = games[:limit]
    total_players = 0
    failed = 0
    for i, g in enumerate(games):
        try:
            box = fetch_boxscore(g["external_id"])
            with session_scope() as session:
                home = process_side(session, box, "home", g["game_id"],
                                    g["home_team_id"], g["away_team_id"], True)
                away = process_side(session, box, "away", g["game_id"],
                                    g["away_team_id"], g["home_team_id"], False)
                total_players += home + away
            if (i + 1) % 10 == 0:
                log.info("progress", processed=i + 1, total=len(games))
        except Exception as e:
            log.error("boxscore_failed", game_pk=g["external_id"], error=str(e))
            failed += 1
    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs
            SET completed_at = NOW(), rows_inserted = :n,
                status = CASE WHEN :failed = 0 THEN 'success' ELSE 'partial' END,
                error_message = CASE WHEN :failed > 0 THEN :emsg ELSE NULL END
            WHERE run_id = :rid
        """), {"n": total_players, "failed": failed,
               "emsg": f"{failed} games failed", "rid": run_id})
    log.info("boxscore_ingest_complete", games=len(games),
             players=total_players, failed=failed)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Max games to process (default: all)")
    args, _ = parser.parse_known_args()
    run(limit=args.limit)
