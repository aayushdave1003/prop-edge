"""Automatic daily walk-forward backtest (runs every morning via daily.sh).

The old ``backtest.py`` compares the model to a *sharp market* and depends on the
``market_odds`` table — which is frozen (the paid odds feed is off), so it can't
produce fresh results day to day. This module instead backtests the system
against its OWN settled history, which grows every night, so it has something
new to say each morning. Three views, all off settled picks + box scores:

  1. Strategy walk-forward — replay the recommended-tier strategy over a rolling
     window: W/L, win% vs the 57.7% 2-pick breakeven, 2-pick ROI, and a
     holding / decaying / improving trend (recent 7d vs the prior 7d).
  2. Model calibration — Brier score + decile calibration (predicted prob vs
     realized win rate) over the window, and per-sport drift (recent vs earlier),
     so a model going stale surfaces before it bleeds the slate.
  3. Counterfactual cutoff sweep — per sport×stat, the cutoff that would have
     maximised 2-pick EV vs the cutoff that's actually live, flagging material
     gaps (a sanity check on the auto-tuner).

MEASUREMENT INTEGRITY (fixed 2026-07-07). The recommended tier is selected
**point-in-time** by ``honest_oos.walk_forward_oos`` — every pick is judged by a
cutoff table fit ONLY on picks that settled *before* it, so no cutoff ever sees
the outcome it is scored against. It previously graded history against
``load_cutoffs()`` (the current table, fit on those same picks) → an in-sample
leak that inflated the number, the exact bug the API/UI were de-leaked for. The
input pool is also gated at the source (forward-only: no lookahead; valid-line:
a real prop line existed), identical to the board. This module now reports the
same honest ~47% the live board does, on a rolling window.

Each run persists one row to ``backtest_daily`` (migration 0007) so the trends
accumulate, and posts a concise Discord digest.

Run:  python -m props.picks.daily_backtest            (window = 45d)
      python -m props.picks.daily_backtest --window 30
"""
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json
import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.utils.config import settings
from props.models.category_cutoffs import load_cutoffs, rec_cutoff, wilson_lower_bound
from props.models.honest_oos import walk_forward_oos
from props.models.prob_calibration import calibrate

BREAKEVEN = 0.577          # per-leg win prob where a 2-pick power play breaks even
MIN_N_SWEEP = 15           # min settled picks before a per-bucket cutoff is trusted
DRIFT_WORSEN = 0.08        # recent calibration gap worse than earlier by this = drift
DRIFT_MIN_GAP = 0.10       # ...and the recent gap itself exceeds this to alert
SWEEP = [0.50 + 0.025 * i for i in range(13)]   # 0.500 → 0.800


def roi_2pick(p: float) -> float:
    """ROI of a 2-pick PrizePicks power play at per-leg win prob p (3x for 2/2)."""
    return 3.0 * p * p - 1.0


def _winrate(w: int, l: int) -> float:
    return w / (w + l) if (w + l) else 0.0


def load_settled(session):
    """ALL settled win/loss picks eligible for an honest evaluation, over the
    FULL history (not just the reporting window) so the point-in-time replay sees
    the same training frontier production did on each day.

    Two source-level gates, identical to ``honest_oos.load_prod_picks`` and the
    live board, so this backtest can't drift from what the product reports:
      - forward-only  (``picked_at < game_datetime``) — no lookahead;
      - valid-line-only (``prop_lines.line_value IS NOT NULL``) — a real prop
        line existed, so a "win" is a real observation.
    Pushes/voids are excluded (they don't inform win rate). Rows are dicts shaped
    for ``walk_forward_oos``: sport / stat_type / direction / model_prob / win /
    decided / settled (plus leg_result for the calibration + sweep views).
    """
    rows = session.execute(text("""
        SELECT (pk.picked_at  AT TIME ZONE 'America/Los_Angeles')::date AS decided,
               (pk.settled_at AT TIME ZONE 'America/Los_Angeles')::date AS settled,
               g.sport_code AS sport, pk.stat_type, pk.direction,
               pk.model_prob::float AS model_prob, pk.leg_result
        FROM picks pk
        JOIN games g USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
        LEFT JOIN player_games pg ON pg.player_id = pk.player_id AND pg.game_id = pk.game_id
        WHERE pk.leg_result IN ('win', 'loss') AND pk.model_prob IS NOT NULL
          AND g.game_datetime IS NOT NULL
          AND pk.picked_at < g.game_datetime   -- forward-only: no lookahead
          AND pl.line_value IS NOT NULL         -- valid-line-only: a real line existed
          AND COALESCE(pg.did_play, true)       -- played-only: a DNP is a void, not win/loss
          AND pl.sportsbook = 'prizepicks'      -- PP-only; Sleeper -> odds_track ROI
        ORDER BY decided
    """)).mappings().all()
    return [{"sport": r["sport"], "stat_type": r["stat_type"],
             "direction": r["direction"], "model_prob": r["model_prob"],
             "win": 1 if r["leg_result"] == "win" else 0,
             "leg_result": r["leg_result"],
             "decided": r["decided"], "settled": r["settled"]} for r in rows]


def walk_forward(all_rows, window_days):
    """Recommended-tier strategy over the reporting window + recent-vs-prior trend.

    Selection is **point-in-time**: ``walk_forward_oos`` replays the FULL history
    (each pick judged by a cutoff fit only on strictly-prior settlements), then we
    report on the trailing ``window_days`` slice. No cutoff sees the window it is
    scored on — this is the same honest number the board shows.
    """
    recommended = walk_forward_oos(all_rows)
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    lo = today - timedelta(days=window_days)

    rec = [r for r in recommended if r["decided"] >= lo]
    allw = [r for r in all_rows if r["decided"] >= lo]
    rw = sum(r["win"] for r in rec)
    rl = len(rec) - rw
    aw = sum(r["win"] for r in allw)
    al = len(allw) - aw

    def _rec_wr(d_lo, d_hi):
        sub = [r for r in rec if d_lo <= r["decided"] <= d_hi]
        w = sum(r["win"] for r in sub)
        n = len(sub)
        return (w / n if n else None), n

    last7_wr, last7_n = _rec_wr(today - timedelta(days=7), today)
    prior7_wr, prior7_n = _rec_wr(today - timedelta(days=14), today - timedelta(days=8))
    trend = "flat"
    if last7_wr is not None and prior7_wr is not None and last7_n >= 5 and prior7_n >= 5:
        delta = last7_wr - prior7_wr
        trend = "improving" if delta > 0.05 else "decaying" if delta < -0.05 else "holding"

    rec_wr = _winrate(rw, rl)
    return {
        "rec_w": rw, "rec_l": rl, "rec_n": rw + rl, "rec_winrate": rec_wr,
        "rec_roi_2pick": roi_2pick(rec_wr),
        "all_w": aw, "all_l": al, "all_n": aw + al, "all_winrate": _winrate(aw, al),
        "trend": trend,
        "last7": {"winrate": last7_wr, "n": last7_n},
        "prior7": {"winrate": prior7_wr, "n": prior7_n},
    }


def calibration(rows):
    """Brier score + decile calibration + per-sport recent-vs-earlier drift.

    model_prob is the model's probability for the pick's chosen direction, so the
    realized outcome is simply win=1 / loss=0."""
    n = len(rows)
    if n == 0:
        return {"brier": None, "brier_cal": None, "buckets": [], "drift": {}}
    brier = sum((r["model_prob"] - r["win"]) ** 2 for r in rows) / n
    # Brier after the live Platt recalibration — confirms the correction helps.
    brier_cal = sum((calibrate(r["model_prob"]) - r["win"]) ** 2 for r in rows) / n

    # decile calibration
    buckets = []
    for i in range(5):                       # 5 buckets of 0.10 from 0.50–1.00
        lo, hi = 0.50 + 0.10 * i, 0.50 + 0.10 * (i + 1)
        sub = [r for r in rows if lo <= r["model_prob"] < hi or (i == 4 and r["model_prob"] >= hi)]
        if not sub:
            continue
        w = sum(r["win"] for r in sub)
        buckets.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": len(sub),
                        "pred": sum(r["model_prob"] for r in sub) / len(sub),
                        "actual": w / len(sub)})

    # per-sport drift: recent half vs earlier half mean (predicted-actual) gap
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    mid = today - timedelta(days=7)
    drift = {}
    sports = sorted({r["sport"] for r in rows})
    for sp in sports:
        srows = [r for r in rows if r["sport"] == sp]
        def _gap(sub):
            if len(sub) < 8:
                return None
            pred = sum(r["model_prob"] for r in sub) / len(sub)
            act = sum(r["win"] for r in sub) / len(sub)
            return pred - act
        recent = _gap([r for r in srows if r["decided"] >= mid])
        earlier = _gap([r for r in srows if r["decided"] < mid])
        if recent is not None or earlier is not None:
            drift[sp] = {"recent_gap": recent, "earlier_gap": earlier}
    return {"brier": brier, "brier_cal": brier_cal, "buckets": buckets, "drift": drift}


def cutoff_sweep(rows, table):
    """Per sport×stat: the cutoff maximising 2-pick EV vs the live cutoff."""
    keyed = {}
    for r in rows:
        keyed.setdefault((r["sport"], r["stat_type"]), []).append(r)

    findings = []
    for (sp, stat), sub in keyed.items():
        if len(sub) < MIN_N_SWEEP:
            continue
        live = rec_cutoff(sp, stat, table)
        # Rank candidate cutoffs by a CONFIDENCE-ADJUSTED EV — 2-pick ROI on the
        # Wilson lower bound, not the raw win rate. A raw 67% on n=18 (Wilson-LB
        # 0.55, below breakeven) is a small-sample mirage; ranking by raw win
        # rate would flag it as a missed edge and tempt us to un-suppress noise.
        # Using the lower bound mirrors how the auto-tuner itself decides, so the
        # backtest never recommends a cutoff the tuner would (correctly) reject.
        best = None
        for c in SWEEP:
            qual = [r for r in sub if r["model_prob"] >= c]
            w = sum(r["win"] for r in qual)
            n = len(qual)
            if n < MIN_N_SWEEP:
                continue
            wr = w / n
            lb = wilson_lower_bound(w, n)
            lb_ev = roi_2pick(lb)
            if best is None or lb_ev > best["lb_ev"]:
                best = {"cutoff": round(c, 3), "n": n, "winrate": wr,
                        "lb": lb, "ev": roi_2pick(wr), "lb_ev": lb_ev}
        if best is None:
            continue
        live_qual = [r for r in sub if r["model_prob"] >= live]
        live_w = sum(r["win"] for r in live_qual)
        live_n = len(live_qual)
        live_wr = (live_w / live_n) if live_n else None
        # "material" only when acting on it is statistically justified:
        #   - the optimal slice must clear breakeven on its Wilson lower bound
        #     (proven, not a hot streak); a still-unproven "best" means
        #     suppression / a high cutoff is correct — not a gap to act on, and
        #   - either the bucket is currently suppressed (no live picks) so that
        #     proven sub-slice is going unused, or the live cutoff is far off and
        #     meaningfully worse than optimal.
        opt_proven = best["lb"] >= BREAKEVEN
        if live_n == 0:
            material = opt_proven
        else:
            material = (opt_proven and abs(best["cutoff"] - live) > 0.05
                        and abs(live_wr - best["winrate"]) > 0.07)
        findings.append({
            "sport": sp, "stat": stat, "live": round(live, 3),
            "live_winrate": live_wr, "live_n": live_n,
            "opt": best, "material": material,
        })
    findings.sort(key=lambda f: (not f["material"], -f["opt"]["ev"]))
    return findings


def persist(session, run_date, window_days, wf, cal, sweep):
    detail = {"trend": wf["trend"], "last7": wf["last7"], "prior7": wf["prior7"],
              "all_w": wf["all_w"], "all_l": wf["all_l"],
              "calibration": cal, "cutoff_sweep": sweep}
    session.execute(text("""
        INSERT INTO backtest_daily
            (run_date, window_days, rec_n, rec_w, rec_l, rec_winrate,
             rec_roi_2pick, all_n, all_winrate, brier, detail)
        VALUES (:rd, :wd, :rn, :rw, :rl, :rwr, :roi, :an, :awr, :brier, :detail)
        ON CONFLICT (run_date) DO UPDATE SET
            window_days=EXCLUDED.window_days, rec_n=EXCLUDED.rec_n,
            rec_w=EXCLUDED.rec_w, rec_l=EXCLUDED.rec_l,
            rec_winrate=EXCLUDED.rec_winrate, rec_roi_2pick=EXCLUDED.rec_roi_2pick,
            all_n=EXCLUDED.all_n, all_winrate=EXCLUDED.all_winrate,
            brier=EXCLUDED.brier, detail=EXCLUDED.detail, created_at=NOW()
    """), {"rd": run_date, "wd": window_days, "rn": wf["rec_n"], "rw": wf["rec_w"],
           "rl": wf["rec_l"], "rwr": wf["rec_winrate"], "roi": wf["rec_roi_2pick"],
           "an": wf["all_n"], "awr": wf["all_winrate"], "brier": cal["brier"],
           "detail": json.dumps(detail)})


_TREND_ICON = {"improving": "↗ improving", "decaying": "↘ decaying",
               "holding": "→ holding", "flat": "→ flat"}


def build_payload(run_date, window_days, wf, cal, sweep):
    ok = wf["rec_winrate"] >= BREAKEVEN
    roi = wf["rec_roi_2pick"]
    desc = (f"**Recommended tier ({window_days}d): {wf['rec_w']}–{wf['rec_l']} "
            f"({wf['rec_winrate']:.0%})** vs 57.7% breakeven {'✅' if ok else '🔻'}\n"
            f"2-pick ROI {roi:+.0%} · trend {_TREND_ICON[wf['trend']]}")
    l7, p7 = wf["last7"], wf["prior7"]
    if l7["winrate"] is not None and p7["winrate"] is not None:
        desc += (f" (7d {l7['winrate']:.0%}/{l7['n']} vs prior "
                 f"{p7['winrate']:.0%}/{p7['n']})")

    fields = []
    if cal["brier"] is not None:
        # most-confident bucket gap as a quick over/under-confidence read
        worst = max((b for b in cal["buckets"] if b["n"] >= 8),
                    key=lambda b: abs(b["pred"] - b["actual"]), default=None)
        cal_line = f"Brier {cal['brier']:.3f} (lower = sharper)"
        if cal.get("brier_cal") is not None:
            cal_line += f" → {cal['brier_cal']:.3f} recalibrated"
        if worst:
            gap = worst["pred"] - worst["actual"]
            tag = "over-confident" if gap > 0.05 else "under-confident" if gap < -0.05 else "well-calibrated"
            cal_line += (f"\n{worst['lo']:.2f}–{worst['hi']:.2f}: predicted "
                         f"{worst['pred']:.0%} vs actual {worst['actual']:.0%} "
                         f"(n={worst['n']}, {tag})")
        fields.append({"name": "Calibration", "value": cal_line, "inline": False})

    material = [f for f in sweep if f["material"]][:3]
    if material:
        lines = []
        for f in material:
            lw = f"{f['live_winrate']:.0%}" if f["live_winrate"] is not None else "n/a"
            lines.append(f"`{f['sport']} {f['stat']}` live {f['live']:.2f} ({lw}) → "
                         f"opt {f['opt']['cutoff']:.2f} ({f['opt']['winrate']:.0%}, "
                         f"ROI {f['opt']['ev']:+.0%})")
        fields.append({"name": "⚠️ Cutoff fit (auto-tuner check)",
                       "value": "\n".join(lines), "inline": False})
    else:
        fields.append({"name": "Cutoff fit",
                       "value": f"✓ all {len(sweep)} buckets within tolerance of live cutoffs",
                       "inline": False})

    # Model-drift alert: a sport whose recent calibration gap (predicted − actual)
    # has worsened materially vs its earlier window — the model is degrading there
    # and likely needs a retrain.
    drift_lines = []
    for sp, d in (cal.get("drift") or {}).items():
        rg, eg = d.get("recent_gap"), d.get("earlier_gap")
        if rg is None or eg is None:
            continue
        if rg - eg > DRIFT_WORSEN and rg > DRIFT_MIN_GAP:
            drift_lines.append(
                f"`{sp}` over-confidence {eg:+.0%} → **{rg:+.0%}** (worsening — "
                "recent picks predicted higher than they hit)")
    if drift_lines:
        fields.append({"name": "🚨 Model drift", "value": "\n".join(drift_lines),
                       "inline": False})

    return {"embeds": [{
        "title": f"🧪 prop-edge — PrizePicks (frozen baseline) — {run_date:%a %b %-d}",
        "description": desc,
        "color": 0x3498db if ok else 0xe67e22,
        "fields": fields,
        "footer": {"text": "point-in-time walk-forward on settled picks · paper-tracking only"},
    }]}


def run(window_days: int = 45, post: bool = True):
    configure_logging()
    run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    table = load_cutoffs()
    with session_scope() as s:
        all_rows = load_settled(s)          # full gated history for point-in-time replay
        if not all_rows:
            log.info("daily_backtest_skipped", reason="no_settled_picks")
            return
        today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
        window_rows = [r for r in all_rows if r["decided"] >= today - timedelta(days=window_days)]
        wf = walk_forward(all_rows, window_days)
        cal = calibration(window_rows)
        sweep = cutoff_sweep(window_rows, table)
        persist(s, run_date, window_days, wf, cal, sweep)
    log.info("daily_backtest_done", rec=f"{wf['rec_w']}-{wf['rec_l']}",
             rec_winrate=round(wf["rec_winrate"], 3), brier=cal["brier"],
             trend=wf["trend"], material=sum(f["material"] for f in sweep))

    if post and settings.discord_webhook_url:
        payload = build_payload(run_date, window_days, wf, cal, sweep)
        try:
            r = requests.post(settings.discord_webhook_url, json=payload, timeout=10)
            log.info("daily_backtest_sent", status=r.status_code)
        except Exception as e:
            log.warning("daily_backtest_post_failed", error=str(e)[:120])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--window", type=int, default=45, help="rolling window in days")
    p.add_argument("--no-post", action="store_true", help="skip the Discord digest")
    args = p.parse_args()
    run(window_days=args.window, post=not args.no_post)


if __name__ == "__main__":
    main()
