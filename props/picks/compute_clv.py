"""Closing Line Value (CLV) — did we pick before the line moved against the field?

CLV is the sharpest long-run signal of edge: win/loss is noisy (56% over 36 picks
means nothing), but if our picks consistently *beat the closing line* — the
line's final value right before tip/first pitch — that's predictive timing that
shows up as profit over a season regardless of short-term variance.

For each settled/started pick we find the LAST prop_lines snapshot before the
game started (the Mac scrape runs ~4x/day incl. ~7pm PT, so the last pre-game
snapshot is a good close proxy) and store it as picks.line_close. CLV in line
points is then:

    over  pick: close - pick_line   (line moved UP  = our over was the easier #)
    under pick: pick_line - close   (line moved DOWN = our under had more room)

Positive = we beat the close. NOTE: PrizePicks isn't a sharp sportsbook, so its
line moves are a softer signal than a true market close — read CLV as a trend,
not gospel.

Run:  python -m props.picks.compute_clv
"""
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging


def clv_points(pick_line, close_line, direction) -> float | None:
    """Signed CLV in line points. Positive = the pick beat the closing line."""
    if pick_line is None or close_line is None:
        return None
    diff = float(close_line) - float(pick_line)
    return diff if direction == "over" else -diff


def run() -> int:
    """Backfill picks.line_close for started games. Returns rows updated."""
    configure_logging()
    with session_scope() as s:
        # Set-based: for each pick missing a close on a started game, grab the
        # latest line snapshot at/ before game start for that player+stat.
        res = s.execute(text("""
            UPDATE picks pk
            SET line_close = sub.line_value
            FROM (
                SELECT pk2.pick_id, cl.line_value
                FROM picks pk2
                JOIN games g ON g.game_id = pk2.game_id
                JOIN LATERAL (
                    SELECT pl.line_value
                    FROM prop_lines pl
                    WHERE pl.player_id = pk2.player_id
                      AND pl.stat_type = pk2.stat_type
                      AND pl.sport_code = pk2.sport_code
                      -- Match the pick's variant. PrizePicks serves standard +
                      -- demon + goblin lines per player at very different values;
                      -- picks are always 'standard', so the close must be too,
                      -- else CLV is garbage (a demon line read as the close).
                      AND pl.line_variant = 'standard'
                      AND pl.snapshot_at <= COALESCE(g.game_datetime,
                                                     g.game_date + INTERVAL '1 day')
                    ORDER BY pl.snapshot_at DESC
                    LIMIT 1
                ) cl ON true
                WHERE pk2.line_close IS NULL
                  AND g.status IN ('final', 'live')
            ) sub
            WHERE pk.pick_id = sub.pick_id
        """))
        n = res.rowcount or 0
    log.info("clv_computed", updated=n)
    return n


if __name__ == "__main__":
    print(f"line_close set for {run()} picks")
