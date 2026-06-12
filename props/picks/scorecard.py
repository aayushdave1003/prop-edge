"""Nightly Discord scorecard — posts last night's results automatically.

Runs in the daily pipeline after settling, so each morning you get a scorecard
in Discord without anyone (or any session) checking manually: recommended-tier
win rate vs the 57.7% 2-pick breakeven, W/L by sport, a 7-day rolling view for
context, and a heads-up on the weakest stat bucket if one is bleeding.

Run:  python -m props.picks.scorecard            (posts for yesterday)
      python -m props.picks.scorecard --date YYYY-MM-DD
"""
import argparse
from datetime import date

import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.config import settings
from props.utils.logging import log, configure_logging
from props.models.category_cutoffs import rec_cutoff

BREAKEVEN = 0.577


def _rows(session, days_back: int):
    return session.execute(text("""
        SELECT (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date AS d,
               g.sport_code, pk.stat_type, pk.direction,
               pk.model_prob, pk.leg_result
        FROM picks pk JOIN games g USING (game_id)
        WHERE pk.leg_result IN ('win', 'loss', 'push')
          AND (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
              >= (NOW() AT TIME ZONE 'America/Los_Angeles')::date - make_interval(days => :d)
    """), {"d": days_back}).all()


def _wl(rows):
    """(wins, losses) over win/loss rows."""
    w = sum(1 for r in rows if r.leg_result == "win")
    l = sum(1 for r in rows if r.leg_result == "loss")
    return w, l


def _rec(rows):
    """Recommended-tier subset (model_prob >= category cutoff)."""
    return [r for r in rows
            if float(r.model_prob) >= rec_cutoff(r.sport_code, r.stat_type)]


def build_payload(rows, target_date):
    day = [r for r in rows if r.d == target_date]
    if not day:
        return None  # nothing settled for that date — skip (no empty spam)

    dw, dl = _wl(day)
    if dw + dl == 0:
        return None
    rec_day = _rec(day)
    rw, rl = _wl(rec_day)
    rec_pct = rw / (rw + rl) if (rw + rl) else 0.0
    ok = rec_pct >= BREAKEVEN

    # per-sport (recommended tier)
    sport_lines = []
    for sp in ("mlb", "nba", "wnba", "nhl"):
        sr = _rec([r for r in day if r.sport_code == sp])
        w, l = _wl(sr)
        if w + l:
            sport_lines.append({"name": {"mlb": "⚾ MLB", "nba": "🏀 NBA",
                                         "wnba": "🏀 WNBA", "nhl": "🏒 NHL"}[sp],
                                "value": f"{w}–{l} ({w/(w+l):.0%})", "inline": True})

    # 7-day rolling recommended tier
    r7w, r7l = _wl(_rec(rows))
    r7 = f"{r7w}–{r7l} ({r7w/(r7w+r7l):.0%})" if (r7w + r7l) else "—"

    # weakest stat bucket over the window (>=8 settled, <50%)
    buckets = {}
    for r in rows:
        if r.leg_result in ("win", "loss"):
            k = (r.sport_code, r.stat_type, r.direction)
            b = buckets.setdefault(k, [0, 0])
            b[0] += r.leg_result == "win"
            b[1] += 1
    weak = sorted(((k, w, n) for k, (w, n) in buckets.items() if n >= 8 and w / n < 0.50),
                  key=lambda x: x[1] / x[2])
    weak_line = ""
    if weak:
        k, w, n = weak[0]
        weak_line = f"\n⚠️ Weak spot (7d): {k[0]} {k[1]} {k[2]} {w}/{n} = {w/n:.0%}"

    fields = sport_lines + [
        {"name": "7-day recommended", "value": r7, "inline": False},
    ]
    desc = (f"**Recommended: {rw}–{rl} ({rec_pct:.0%})** vs 57.7% breakeven "
            f"{'✅' if ok else '🔻'}\nOverall: {dw}–{dl} ({dw/(dw+dl):.0%})"
            f"{weak_line}")
    return {"embeds": [{
        "title": f"📊 prop-edge scorecard — {target_date:%a %b %-d}",
        "description": desc,
        "color": 0x2ecc71 if ok else 0xe67e22,
        "fields": fields,
        "footer": {"text": "auto-posted nightly · paper-tracking only"},
    }]}


def run(target_date: date = None):
    configure_logging()
    if not settings.discord_webhook_url:
        log.info("scorecard_skipped", reason="no_webhook")
        return
    with session_scope() as s:
        rows = _rows(s, days_back=7)
    if target_date is None:
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        from datetime import datetime
        target_date = (datetime.now(ZoneInfo("America/Los_Angeles")).date()
                       - timedelta(days=1))
    payload = build_payload(rows, target_date)
    if payload is None:
        log.info("scorecard_skipped", reason="no_settled_picks", date=str(target_date))
        return
    try:
        r = requests.post(settings.discord_webhook_url, json=payload, timeout=10)
        log.info("scorecard_sent", status=r.status_code, date=str(target_date))
    except Exception as e:
        log.warning("scorecard_failed", error=str(e)[:120])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="Pick date to report (YYYY-MM-DD)")
    args = p.parse_args()
    td = date.fromisoformat(args.date) if args.date else None
    run(target_date=td)


if __name__ == "__main__":
    main()
