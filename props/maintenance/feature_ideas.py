"""Weekly 'feature ideas' digest to Discord.

So you get *nudged* about what to build next without ever checking the dashboard:
this inspects the live system once a week and posts data-driven opportunities —
a sport that's accumulated enough games to train a winner model, a stat bucket
that's running hot (worth leaning into) or cold (worth a fix), a suppressed
category that looks ready to come back, the Odds-API gap, etc.

Every idea is grounded in the current data, not generic. Wired into daily.sh's
Monday branch. Run standalone: python -m props.maintenance.feature_ideas
"""
import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.config import settings
from props.utils.logging import log, configure_logging
from props.models.category_cutoffs import load_cutoffs

# Sports we already have a winner model for (so we only pitch the missing ones).
HAVE_WINNER_MODEL = {"nba", "mlb"}
WINNER_TRAINABLE_GAMES = 120   # enough final games to train an honest winner model
HOT_WR, COLD_WR = 0.66, 0.45   # bucket thresholds over the trailing window
MIN_BUCKET_N = 12


def _ideas(s) -> list[str]:
    ideas = []

    # 1. Winner models that just became trainable (NHL/WNBA).
    for sport, n in s.execute(text("""
            SELECT sport_code, COUNT(*) FROM games
            WHERE status='final' GROUP BY sport_code""")).all():
        if sport not in HAVE_WINNER_MODEL and n >= WINNER_TRAINABLE_GAMES:
            ideas.append(f"🏆 **{sport.upper()} winner model** is now trainable "
                         f"({n} final games). Want me to build it?")

    # 2. Hot / cold stat buckets over the last 21 days.
    rows = s.execute(text("""
        SELECT g.sport_code, pk.stat_type, pk.direction,
               COUNT(*) FILTER (WHERE pk.leg_result='win') w,
               COUNT(*) FILTER (WHERE pk.leg_result IN ('win','loss')) n
        FROM picks pk JOIN games g USING (game_id)
        WHERE pk.picked_at > NOW() - INTERVAL '21 days'
        GROUP BY 1,2,3
        HAVING COUNT(*) FILTER (WHERE pk.leg_result IN ('win','loss')) >= :m
    """), {"m": MIN_BUCKET_N}).all()
    hot = sorted(((r.sport_code, r.stat_type, r.direction, r.w / r.n, r.n)
                  for r in rows if r.w / r.n >= HOT_WR), key=lambda x: -x[3])
    cold = sorted(((r.sport_code, r.stat_type, r.direction, r.w / r.n, r.n)
                   for r in rows if r.w / r.n <= COLD_WR), key=lambda x: x[3])
    if hot:
        sp, st, d, wr, n = hot[0]
        ideas.append(f"🔥 **{sp} {st} {d}** is hot ({wr:.0%}, n={n}, 21d) — worth a "
                     f"dedicated model or heavier weighting.")
    if cold:
        sp, st, d, wr, n = cold[0]
        ideas.append(f"🧊 **{sp} {st} {d}** is cold ({wr:.0%}, n={n}, 21d) — the cutoffs "
                     f"auto-suppress it, but a model feature could fix the root cause.")

    # 3. Suppressed categories that look like they're recovering.
    tbl = load_cutoffs()
    for key, info in tbl.get("stats", {}).items():
        if info.get("status") != "suppressed":
            continue
        sport, stat = key.split("|", 1)
        r = s.execute(text("""
            SELECT COUNT(*) FILTER (WHERE pk.leg_result='win') w,
                   COUNT(*) FILTER (WHERE pk.leg_result IN ('win','loss')) n
            FROM picks pk JOIN games g USING (game_id)
            WHERE g.sport_code=:sp AND pk.stat_type=:st
              AND pk.picked_at > NOW() - INTERVAL '14 days'
        """), {"sp": sport, "st": stat}).first()
        if r and r.n >= 10 and r.w / r.n >= 0.58:
            ideas.append(f"📈 **{sport} {stat}** is suppressed but {r.w/r.n:.0%} over the "
                         f"last 14d (n={r.n}) — may be ready to un-suppress / revisit.")

    # 4. Odds API gap (market_edge flat).
    mx = s.execute(text("SELECT MAX(snapshot_time) FROM market_odds")).scalar()
    stale = s.execute(text(
        "SELECT MAX(snapshot_time) < NOW() - INTERVAL '7 days' FROM market_odds")).scalar()
    if mx is None or stale:
        ideas.append("📊 **Odds API is off** — `market_edge` is flat. Re-upping the plan "
                     "adds a real market-vs-model signal (the biggest accuracy lever left).")

    return ideas


def run():
    configure_logging()
    if not settings.discord_webhook_url:
        log.info("feature_ideas_skipped", reason="no_webhook")
        return
    with session_scope() as s:
        ideas = _ideas(s)
    if not ideas:
        log.info("feature_ideas_none")
        return
    payload = {"embeds": [{
        "title": "💡 prop-edge — feature ideas this week",
        "description": "\n\n".join(f"• {i}" for i in ideas[:5])
                       + "\n\n_Reply to your Claude session to build any of these._",
        "color": 0x9b59b6,
        "footer": {"text": "auto-generated weekly · grounded in your live data"},
    }]}
    try:
        r = requests.post(settings.discord_webhook_url, json=payload, timeout=10)
        log.info("feature_ideas_sent", status=r.status_code, ideas=len(ideas))
    except Exception as e:
        log.warning("feature_ideas_failed", error=str(e)[:120])


if __name__ == "__main__":
    run()
