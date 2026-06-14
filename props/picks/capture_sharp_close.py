"""Capture the sharp market's closing probability for today's open picks.

Run LATE (intraday refresh, near game time): fetches live sharp odds and records,
per still-open pick, the sharp no-vig probability for the pick's side at the
pick's exact line — into picks.market_prob_close. Combined with picks.market_prob
(the same quantity at pick time), this gives **sharp-market CLV**:

    sharp_clv = market_prob_close − market_prob   (for the picked side)

Positive = the sharp market moved toward our side after we picked, i.e. we beat
the close — the gold-standard long-run edge signal (unlike the existing
PrizePicks line_close, which is sticky and barely moves).

Uses the SAME exact-line lookup that priced market_prob at pick time, so the two
are directly comparable (only picks where a sharp line exists at the pick's
number get a CLV — that's fine, it's a trend over a sample).

Run:  python -m props.picks.capture_sharp_close
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.ingest.market_odds import build_market_probs


def run():
    configure_logging()
    run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    market = build_market_probs(run_date)        # {(name_lower, stat, line): over_prob}
    if not market:
        log.info("capture_sharp_close_skipped", reason="no_market_data")
        return

    with session_scope() as s:
        # Open picks on games that haven't gone final yet, today's slate.
        picks = s.execute(text("""
            SELECT pk.pick_id, lower(p.full_name) AS name, pk.stat_type,
                   pk.direction, pl.line_value::float AS line
            FROM picks pk
            JOIN players p USING (player_id)
            JOIN prop_lines pl ON pl.line_id = pk.line_id
            JOIN games g ON g.game_id = pk.game_id
            WHERE g.game_date = :d AND g.status <> 'final'
              AND pk.leg_result IS NULL
        """), {"d": run_date}).all()

        updated = 0
        for pk in picks:
            over = market.get((pk.name, pk.stat_type, pk.line))
            if over is None:
                continue
            side_prob = over if pk.direction == "over" else 1.0 - over
            s.execute(text(
                "UPDATE picks SET market_prob_close = :p WHERE pick_id = :id"),
                {"p": round(float(side_prob), 4), "id": pk.pick_id})
            updated += 1
    log.info("capture_sharp_close_done", open_picks=len(picks), captured=updated)


if __name__ == "__main__":
    run()
