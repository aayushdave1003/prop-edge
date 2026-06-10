"""Settle unsettled picks against final game outcomes.

For each pick where leg_result IS NULL:
  - Find the corresponding player_game row
  - If game.status == 'final', read actual stat value
  - Compare to line, factor in direction (over/under), classify win/loss/push
  - Update the pick with actual_value, leg_result, settled_at
"""
from datetime import datetime, date, timedelta
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging


COMBO_STAT_MAP = {
    "pts_rebs_asts": ["points", "rebounds", "assists"],
    "pts_rebs":      ["points", "rebounds"],
    "pts_asts":      ["points", "assists"],
    "rebs_asts":     ["rebounds", "assists"],
    "blocks_steals": ["blocks", "steals"],
}

def _resolve_stat(stats_json: dict, stat_type: str):
    """Return actual stat value, handling combo stats like pts_rebs_asts."""
    if stat_type in COMBO_STAT_MAP:
        parts = COMBO_STAT_MAP[stat_type]
        values = [stats_json.get(p) for p in parts]
        if any(v is None for v in values):
            return None
        return sum(float(v) for v in values)
    return stats_json.get(stat_type)


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
            LEFT JOIN prop_lines pl ON pl.line_id = pk.line_id
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


def resolve_placeholder_game_ids():
    """Re-point unsettled picks from placeholder/non-final games to the player's
    real final game on the same date. Makes the daily settle self-healing so we
    never have to hand-fix pp_xxx game_ids again.

    Matches each unsettled pick to a player_game where the player actually played
    a FINAL game within a 2-day window of the pick date (handles UTC date crossover).
    """
    with session_scope() as session:
        result = session.execute(text("""
            WITH pick_fix AS (
                SELECT DISTINCT ON (pk.pick_id) pk.pick_id, pg.game_id AS real_game_id
                FROM picks pk
                JOIN games cur ON cur.game_id = pk.game_id
                JOIN player_games pg ON pg.player_id = pk.player_id
                JOIN games g ON g.game_id = pg.game_id
                WHERE pk.leg_result IS NULL
                  AND cur.external_id LIKE 'pp_%'
                  AND g.status = 'final'
                  AND g.sport_code = cur.sport_code
                  AND g.game_date BETWEEN
                        (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date - INTERVAL '1 day'
                    AND (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date + INTERVAL '1 day'
                  AND g.game_id <> pk.game_id
                ORDER BY pk.pick_id, g.game_date
            )
            UPDATE picks pk
            SET game_id = pf.real_game_id
            FROM pick_fix pf
            WHERE pk.pick_id = pf.pick_id
        """))
        log.info("resolved_placeholder_game_ids", repointed=result.rowcount)


def run():
    configure_logging()
    resolve_placeholder_game_ids()
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
                # A game still not final 3+ days after its date is abandoned (a
                # postponed/dead placeholder, e.g. unresolved pp_ rows) and will
                # never settle — void rather than wait forever. Today's/upcoming
                # games still legitimately wait.
                if game_date is not None and game_date < date.today() - timedelta(days=2):
                    session.execute(text("""
                        UPDATE picks SET leg_result='void', settled_at=NOW()
                        WHERE pick_id=:pid
                    """), {"pid": pick_id})
                    log.info("voided_stale_unplayed", pick_id=pick_id,
                             player=player_name, game_date=str(game_date))
                    settled += 1
                else:
                    waiting += 1
                continue

            if line_value is None:
                # The prop_line this pick referenced is gone (old prune/cleanup),
                # and INNER-joining it used to silently drop the pick from the
                # queue forever. Without the line we can't classify win/loss, so
                # void it — a final-game pick with no line is unsettleable.
                session.execute(text("""
                    UPDATE picks SET leg_result='void', settled_at=NOW()
                    WHERE pick_id=:pid
                """), {"pid": pick_id})
                log.info("voided_missing_line", pick_id=pick_id, player=player_name,
                         game_date=str(game_date))
                settled += 1
                continue

            if stats_json is None:
                # Game is final but player has no box score row — they were scratched/DNP.
                # Void the pick so it doesn't block the unsettled queue forever.
                session.execute(text("""
                    UPDATE picks SET leg_result='void', settled_at=NOW()
                    WHERE pick_id=:pid
                """), {"pid": pick_id})
                log.info("voided_dnp", pick_id=pick_id, player=player_name,
                         game_date=str(game_date))
                settled += 1
                continue

            # Void pitcher strikeout picks when the player never took the mound.
            # Two-way players (e.g. Ohtani) have a batting row with batters_faced=0
            # on days they DH only — don't count 0 Ks as an under win.
            if stat_type == "strikeouts_pitcher" and stats_json.get("batters_faced", 0) == 0:
                session.execute(text("""
                    UPDATE picks SET leg_result='void', settled_at=NOW()
                    WHERE pick_id=:pid
                """), {"pid": pick_id})
                log.info("voided_did_not_pitch", pick_id=pick_id, player=player_name,
                         game_date=str(game_date))
                settled += 1
                continue

            actual_raw = _resolve_stat(stats_json, stat_type)
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
