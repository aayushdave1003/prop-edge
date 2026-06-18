"""Nightly Discord scorecard — posts last night's results automatically.

Runs in the daily pipeline after settling, so each morning you get a scorecard
in Discord without anyone (or any session) checking manually: recommended-tier
win rate vs the 57.7% 2-pick breakeven, W/L by sport, a 7-day rolling view for
context, and a heads-up on the weakest stat bucket if one is bleeding.

Run:  python -m props.picks.scorecard            (posts for yesterday)
      python -m props.picks.scorecard --date YYYY-MM-DD
"""
import argparse
import math
from datetime import date

import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.config import settings
from props.utils.logging import log, configure_logging
from props.models.category_cutoffs import rec_cutoff

BREAKEVEN = 0.577
WIN_PL = math.sqrt(3.0) - 1.0   # per-leg P&L of a 2-pick 3x parlay on a win
DD_UNITS_ALERT = 6.0            # flag a paper drawdown this deep (in units)
STREAK_ALERT = 6               # …or a losing streak this long


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


def _drawdown(rec_rows):
    """Flat 1u paper P&L over rec-tier picks (chronological). Returns
    (current_drawdown_units, current_losing_streak)."""
    rows = sorted([r for r in rec_rows if r.leg_result in ("win", "loss")],
                  key=lambda r: r.d)
    cum = peak = 0.0
    streak = 0
    for r in rows:
        if r.leg_result == "win":
            cum += WIN_PL
            streak = 0
        else:
            cum -= 1.0
            streak += 1
        peak = max(peak, cum)
    return round(peak - cum, 1), streak


def _accuracy_block(rows) -> str:
    """Monospace accuracy table over the loaded window: overall hit rate,
    recommended-tier breakdown (overall + by sport), and per sport × line."""
    win = [r for r in rows if r.leg_result in ("win", "loss")]
    aw, al = _wl(win)
    rw, rl = _wl(_rec(win))
    _dts = [r.d for r in win]
    days = (max(_dts) - min(_dts)).days + 1 if _dts else 0
    L = [f"ACCURACY · last {days}d"]
    if aw + al:
        L.append(f"all picks      {aw}-{al}  ({100*aw/(aw+al):.0f}%)  ·  {aw} of {aw+al} hit")
    if rw + rl:
        L.append(f"recommended    {rw}-{rl}  ({100*rw/(rw+rl):.0f}%)  vs 57.7% breakeven")
        for sp in ("mlb", "nba", "wnba", "nhl"):
            w, l = _wl(_rec([r for r in win if r.sport_code == sp]))
            if w + l:
                L.append(f"   {sp:5} {w}-{l} ({100*w/(w+l):.0f}%)")
    # per sport × line (stat/direction), all picks, n>=5
    buckets: dict = {}
    for r in win:
        b = buckets.setdefault((r.sport_code, r.stat_type, r.direction), [0, 0])
        b[0] += r.leg_result == "win"; b[1] += 1
    items = sorted(((k, w, n) for k, (w, n) in buckets.items() if n >= 5),
                   key=lambda t: (t[0][0], -t[2]))
    _short = {"strikeouts_pitcher": "strikeouts", "strikeouts_batter": "K(bat)",
              "total_bases": "tot_bases", "pts_rebs_asts": "PRA", "pts_rebs": "P+R",
              "pts_asts": "P+A", "rebs_asts": "R+A", "blocks_steals": "blk+stl",
              "threes_made": "3PM", "fg3_made": "3PM"}
    if items:
        L += ["", "by sport · line (n≥5):"]
        for (sp, stat, d), w, n in items[:22]:
            L.append(f"  {sp:4} {_short.get(stat, stat)[:12]:12} {d:5} {w}-{n-w:<2} {100*w/n:>3.0f}%")
    return "```\n" + "\n".join(L)[:1010] + "\n```"


def build_payload(rows, target_date, today=None):
    from datetime import timedelta
    if today is None:
        today = target_date + timedelta(days=1)
    last7 = [r for r in rows if r.d >= today - timedelta(days=7)]
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
    r7w, r7l = _wl(_rec(last7))
    r7_wr = r7w / (r7w + r7l) if (r7w + r7l) else 1.0
    r7 = f"{r7w}–{r7l} ({r7_wr:.0%})" if (r7w + r7l) else "—"

    # Cold-stretch alert over the 30-day recommended history. A long losing
    # streak always flags; a deep paper drawdown only flags if recent form is
    # ALSO below breakeven (so a stale-but-recovering drawdown stays quiet).
    dd, streak = _drawdown(_rec(rows))
    cold = []
    if streak >= STREAK_ALERT:
        cold.append(f"{streak}-loss streak")
    if dd >= DD_UNITS_ALERT and r7_wr < BREAKEVEN:
        cold.append(f"down {dd:.1f}u from peak")
    dd_line = f"\n🥶 **Cold stretch** — {' · '.join(cold)}" if cold else ""

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
        {"name": "🎯 Accuracy", "value": _accuracy_block(rows), "inline": False},
    ]
    desc = (f"**Recommended: {rw}–{rl} ({rec_pct:.0%})** vs 57.7% breakeven "
            f"{'✅' if ok else '🔻'}\nOverall: {dw}–{dl} ({dw/(dw+dl):.0%})"
            f"{dd_line}{weak_line}")
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
    from datetime import timedelta, datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    with session_scope() as s:
        rows = _rows(s, days_back=30)
    if target_date is None:
        target_date = today - timedelta(days=1)
    payload = build_payload(rows, target_date, today)
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
