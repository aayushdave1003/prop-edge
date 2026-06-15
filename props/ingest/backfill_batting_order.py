"""One-off backfill of bat_order_spot into player_games.stats for past MLB games.

The daily box-score ingest only re-touches recent games, so historical
player_games predate the bat_order_spot capture. The models train on full history,
so the feature is useless until the past is filled. This re-fetches each final
MLB game's boxscore, reads battingOrder, and merges bat_order_spot into stats.

Resumable: skips games whose player_games already carry bat_order_spot, commits
per game, so it can be re-run / interrupted freely. Recent games first (so the
A/B test window fills before the deep history).

Run:  python -m props.ingest.backfill_batting_order [--limit N] [--sleep 0.05]
"""
import argparse
import json
import time

from sqlalchemy import text

from props.ingest.mlb_boxscores import fetch_boxscore
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging


def _spots_from_box(box) -> dict:
    """external_player_id (str) -> batting-order spot (1..9, 0 = didn't bat)."""
    spots = {}
    for side in ("home", "away"):
        for _, pdata in box.get("teams", {}).get(side, {}).get("players", {}).items():
            ext = str((pdata.get("person") or {}).get("id"))
            bo = pdata.get("battingOrder")
            spots[ext] = int(bo) // 100 if bo and str(bo).isdigit() else 0
    return spots


def games_to_backfill(session, limit=None):
    rows = session.execute(text("""
        SELECT g.game_id, g.external_id
        FROM games g
        WHERE g.sport_code = 'mlb' AND g.status = 'final'
          AND EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id
                      AND pg.stats ? 'at_bats' AND NOT (pg.stats ? 'bat_order_spot'))
        ORDER BY g.game_date DESC
    """)).all()
    rows = [(r[0], r[1]) for r in rows if r[1] and not str(r[1]).startswith("pp_")]
    return rows[:limit] if limit else rows


def run(limit=None, sleep=0.05):
    configure_logging()
    with session_scope() as s:
        games = games_to_backfill(s, limit)
    log.info("batting_order_backfill_start", games=len(games))
    done = updated = failed = 0
    for i, (game_id, ext) in enumerate(games):
        try:
            spots = _spots_from_box(fetch_boxscore(ext))
            with session_scope() as s:
                rows = s.execute(text("""
                    SELECT pg.player_game_id, p.external_id
                    FROM player_games pg JOIN players p USING (player_id)
                    WHERE pg.game_id = :gid
                """), {"gid": game_id}).all()
                for pg_id, pext in rows:
                    spot = spots.get(str(pext))
                    if spot is None:
                        continue
                    s.execute(text("""
                        UPDATE player_games
                        SET stats = stats || CAST(:patch AS JSONB)
                        WHERE player_game_id = :id
                    """), {"patch": json.dumps({"bat_order_spot": spot}), "id": pg_id})
                    updated += 1
            done += 1
            if (i + 1) % 200 == 0:
                log.info("batting_order_backfill_progress",
                         processed=i + 1, total=len(games), rows=updated)
            if sleep:
                time.sleep(sleep)
        except Exception as e:
            failed += 1
            log.warning("batting_order_backfill_game_failed",
                        game_id=game_id, error=str(e)[:120])
    log.info("batting_order_backfill_complete",
             games=done, rows_updated=updated, failed=failed)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.05,
                    help="delay between game fetches (be nice to statsapi)")
    args = ap.parse_args()
    run(limit=args.limit, sleep=args.sleep)
