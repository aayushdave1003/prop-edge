"""Settle unsettled picks against final game outcomes.

For each pick where leg_result IS NULL:
  - Find the corresponding player_game row
  - If game.status == 'final', read actual stat value
  - Compare to line, factor in direction (over/under), classify win/loss/push
  - Update the pick with actual_value, leg_result, settled_at
"""
from datetime import datetime
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging


def find_unsettled_picks():
    """Return list of picks rows joined to game status and actual stat."""
    with session_scope() as session:
        rows = session.execute(text("""
            SELECT pk.pick_id, pk.stat_type, pk.direction,
                   pl.line_value,
                   pg.player_game_id, pg.stats,
                   g.status, g.game_date,
                   pl_player.full_name AS player_name
            FROM picks pk
            JOIN prop_lines pl ON pl.line_id = pk.line_id
            LEFT JOIN player_games pg
                ON pg.player_id = pk.player_id AND pg.game_id = pk.game_id
            LEFT JOIN games g ON g.game_id = pk.game_id
            LEFT JOIN players pl_player ON pl_player.player_id = pk.player_id
            WHERE pk.leg_result IS NULL
            ORDER BY pk.picked_at DESC
        """)).all()
    return rows


def classify(actual_value: float, line_value: float, direction: str) -> str:
    """Return 'win', 'loss', or 'push'."""
    if actual_value == line_value:
        return "push"
    if direction == "over":
        return "win" if actual_value > line_value else "loss"
    else:  # under
        return "win" if actual_value < line_value else "loss"


def settle_one(session, pick_id: int, stat_type: str, direction: str,
               line_value: float, actual_value: float):
    result = classify(float(actual_value), float(line_value), direction)
    session.execute(text("""
        UPDATE picks
        SET actual_value = :av,
            leg_result = :res,
            settled_at = NOW()
        WHERE pick_id = :pid
    """), {"av": actual_value, "res": result, "pid": pick_id})
    return result


def run():
    configure_logging()
    rows = find_unsettled_picks()
    log.info("found_unsettled", n=len(rows))

    if not rows:
        log.info("nothing_to_settle")
        return

    settled = 0
    waiting = 0
    missing = 0

    with session_scope() as session:
        for r in rows:
            (pick_id, stat_type, direction, line_value,
             player_game_id, stats_json, game_status, game_date, player_name) = r

            if game_status is None:
                log.warning("game_missing", pick_id=pick_id)
                missing += 1
                continue

            if game_status != "final":
                waiting += 1
                continue

            if stats_json is None:
                log.warning("no_player_game_row", pick_id=pick_id,
                            player=player_name, game_date=str(game_date))
                missing += 1
                continue

            actual_raw = stats_json.get(stat_type)
            if actual_raw is None:
                log.warning("stat_not_in_box", pick_id=pick_id,
                            stat=stat_type, player=player_name)
                missing += 1
                continue

            actual_value = float(actual_raw)
            result = settle_one(session, pick_id, stat_type, direction,
                                line_value, actual_value)
            log.info("settled", pick_id=pick_id, player=player_name,
                     stat=stat_type, line=float(line_value),
                     actual=actual_value, direction=direction, result=result)
            settled += 1

    log.info("settlement_complete",
             settled=settled, waiting_for_final=waiting, missing_data=missing)


if __name__ == "__main__":
    run()
