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
from props.utils.config import settings
from props.ingest.market_odds import build_market_probs

STEAM_THRESHOLD = 0.08   # sharp-prob move (pp) since pick time worth alerting on


def run():
    configure_logging()
    run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    market = build_market_probs(run_date)        # {(name_lower, stat, line): over_prob}
    if not market:
        log.info("capture_sharp_close_skipped", reason="no_market_data")
        return

    steam = []   # notable line moves since we picked (player, side, stat, line, delta)
    with session_scope() as s:
        # Open picks on games that haven't gone final yet, today's slate.
        picks = s.execute(text("""
            SELECT pk.pick_id, p.full_name AS player, lower(p.full_name) AS name,
                   pk.stat_type, pk.direction, pl.line_value::float AS line,
                   pk.market_prob::float AS pick_prob
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
            # Steam: the sharp prob for OUR side moved materially since pick time.
            if pk.pick_prob is not None and abs(side_prob - pk.pick_prob) >= STEAM_THRESHOLD:
                steam.append((pk.player, pk.direction, pk.line, pk.stat_type,
                              side_prob - float(pk.pick_prob)))
    log.info("capture_sharp_close_done", open_picks=len(picks), captured=updated,
             steam=len(steam))
    _alert_steam(run_date, steam)


def _alert_steam(run_date, steam):
    """Discord ping for notable line moves: the sharp market moving TOWARD our
    side (confirmation) or AGAINST it (caution) after we logged the pick."""
    if not steam or not settings.discord_webhook_url:
        return
    import requests
    steam.sort(key=lambda x: -abs(x[4]))
    lines = []
    for player, direction, line, stat, delta in steam[:8]:
        arrow = "📈 toward us" if delta > 0 else "📉 against us"
        lines.append(f"`{direction.upper()} {line:g} {stat}` **{player}** — "
                     f"sharp {delta*100:+.0f}pp {arrow}")
    payload = {"embeds": [{
        "title": f"🌊 Line moves on today's picks — {run_date:%a %b %-d}",
        "description": "Sharp market moved on logged picks since we took them:\n"
                       + "\n".join(lines),
        "color": 0x3498db,
        "footer": {"text": "+toward us = the market agrees · paper-tracking only"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
        log.info("steam_alert_sent", n=len(steam))
    except Exception as e:
        log.warning("steam_alert_failed", error=str(e)[:120])


if __name__ == "__main__":
    run()
