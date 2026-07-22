"""Market-arbitrage finder — Sleeper (an ODDS book) vs the sharp market.

The model has no edge (resolution ~0), so this is model-INDEPENDENT: it bets where
Sleeper's posted payout OVERPAYS vs the sharp market's true probability. A pick is

    +EV  iff  sharp_true_prob × sleeper_payout > 1

The sharp prob is the DK/FD no-vig consensus, captured PRE-GAME (the live morning
fetch), converted to Sleeper's exact line via the market-implied Poisson mean. That
makes it leak-free — a clean backtest (odds snapshotted at each game's start−2h) put
the +EV tier at breakeven-to-slightly-positive vs a −12% bet-everything baseline.
(The 22:00-UTC HISTORICAL snapshot leaked: it's post-game for day games, so the
"edge" was hindsight. This finder never uses that path — only the live AM fetch.)

Guards against phantom edges: the sharp anchor must be a liquid main line (no-vig
prob in [0.20, 0.80]) and EV is capped at 40% (anything larger is almost always a
bad anchor, not a real 40% edge). Only hits/total_bases — the MLB batter markets
DK/FD actually price on the current Odds API plan.

Run:  python -m props.picks.sleeper_arb          # find + persist + digest
      python -m props.picks.sleeper_arb --roi     # forward realized-ROI report
      python -m props.picks.sleeper_arb --no-post
"""
import argparse
import math
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from props.utils.db import session_scope, engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging
from props.ingest.market_odds import build_market_probs
from props.picks.soft_lines import implied_lambda, p_over_at
from props.models.odds_track import MIN_TIER_N

MIN_EDGE = 0.02            # only surface a real margin over the payout's breakeven
MAX_EDGE = 0.40            # bigger "edge" = almost always a bad anchor, not real
ANCHOR_MIN, ANCHOR_MAX = 0.20, 0.80
STATS = ("hits", "total_bases")


def _load_sleeper_lines(session, run_date):
    """Today's Sleeper hits/TB lines (latest snapshot) with player + payouts."""
    return session.execute(text("""
        SELECT DISTINCT ON (pl.player_id, pl.stat_type, pl.line_value)
               pl.player_id, p.full_name AS player_name, pl.game_id, g.sport_code,
               pl.stat_type, pl.line_value::float AS line,
               pl.over_payout::float AS op, pl.under_payout::float AS up
        FROM prop_lines pl
        JOIN players p USING (player_id)
        JOIN games g ON g.game_id = pl.game_id
        WHERE pl.sportsbook = 'sleeper' AND pl.stat_type = ANY(:stats)
          AND pl.line_variant = 'standard'
          AND pl.over_payout IS NOT NULL AND pl.under_payout IS NOT NULL
          AND g.game_date = :d
          -- LEAK GUARD: only games that haven't started (+30m buffer). The sharp
          -- prob and the outcome are only both unknown pre-game — this keeps the
          -- finder clean even if it's ever run mid-slate, not just in the AM cron.
          AND g.game_datetime > NOW() + INTERVAL '30 minutes'
        ORDER BY pl.player_id, pl.stat_type, pl.line_value, pl.snapshot_at DESC
    """), {"stats": list(STATS), "d": run_date}).all()


def _sharp_by_player_stat(market: dict) -> dict:
    """{(name,stat,line):over_prob} -> {(name,stat): [(line, over_prob), ...]}."""
    out: dict = {}
    for (name, stat, line), prob in market.items():
        out.setdefault((name, stat), []).append((float(line), float(prob)))
    return out


def compute_arb(sleeper_lines, sharp_by_ps) -> list[dict]:
    """+EV arbitrage picks: sharp_true_prob × sleeper_payout > 1, guarded."""
    findings = []
    for r in sleeper_lines:
        sharp = sharp_by_ps.get((r.player_name.lower().strip(), r.stat_type))
        if not sharp:
            continue
        # anchor on the sharp MAIN line (no-vig prob nearest 0.5); skip alt/thin.
        s_line, s_prob = min(sharp, key=lambda lp: abs(lp[1] - 0.5))
        if not (ANCHOR_MIN <= s_prob <= ANCHOR_MAX):
            continue
        lam = implied_lambda(s_line, s_prob)
        if lam is None:
            continue
        p_over = p_over_at(r.line, lam)
        ev_over, ev_under = p_over * r.op - 1.0, (1.0 - p_over) * r.up - 1.0
        if ev_over >= ev_under:
            side, ev, payout, prob = "over", ev_over, r.op, p_over
        else:
            side, ev, payout, prob = "under", ev_under, r.up, 1.0 - p_over
        if not (MIN_EDGE < ev < MAX_EDGE):
            continue
        findings.append({
            "player_id": r.player_id, "player_name": r.player_name, "game_id": r.game_id,
            "stat_type": r.stat_type, "line": round(r.line, 2), "side": side,
            "sharp_prob": round(prob, 4), "payout": round(payout, 3), "ev": round(ev, 4),
            "sharp_line": round(s_line, 2), "sharp_over_prob": round(s_prob, 4),
        })
    findings.sort(key=lambda f: f["ev"], reverse=True)
    return findings


def _persist(session, run_date, findings):
    session.execute(text("DELETE FROM sleeper_arb WHERE run_date = :d"), {"d": run_date})
    for f in findings:
        session.execute(text("""
            INSERT INTO sleeper_arb (run_date, player_id, game_id, stat_type, line_value,
                side, sharp_prob, payout, ev, sharp_line, sharp_over_prob)
            VALUES (:d, :pid, :gid, :st, :ln, :side, :sp, :pay, :ev, :sl, :sop)
            ON CONFLICT (run_date, player_id, stat_type, line_value) DO UPDATE SET
                side=EXCLUDED.side, sharp_prob=EXCLUDED.sharp_prob, payout=EXCLUDED.payout,
                ev=EXCLUDED.ev, sharp_line=EXCLUDED.sharp_line,
                sharp_over_prob=EXCLUDED.sharp_over_prob, created_at=NOW()
        """), {"d": run_date, "pid": f["player_id"], "gid": f["game_id"], "st": f["stat_type"],
               "ln": f["line"], "side": f["side"], "sp": f["sharp_prob"], "pay": f["payout"],
               "ev": f["ev"], "sl": f["sharp_line"], "sop": f["sharp_over_prob"]})


def _post_discord(run_date, findings):
    if not settings.discord_webhook_url or not findings:
        return
    lines = [f"`{f['side'].upper()} {f['line']:g} {f['stat_type']}` **{f['player_name']}** "
             f"— sharp {f['sharp_prob']:.0%} @ {f['payout']:.2f}x · +{f['ev']*100:.0f}% EV"
             for f in findings[:8]]
    # running forward track record (picks settled so far), gated so a thin sample
    # reports "building" rather than a phantom verdict.
    s = arb_roi()
    if s["n"] >= MIN_TIER_N:
        rec = f"\n\n**Track record:** ROI {s['roi']:+.1%} [{s['lo']:+.1%}, {s['hi']:+.1%}] over {s['n']} settled · {s['verdict']}"
    elif s["n"]:
        rec = f"\n\n**Track record:** building — {s['n']}/{MIN_TIER_N} settled +EV picks (ROI held until the tier fills)"
    else:
        rec = "\n\n**Track record:** starts accumulating as today's picks settle."
    payload = {"embeds": [{
        "title": f"🧮 Sleeper arbitrage — {run_date:%a %b %-d}",
        "description": ("Sleeper lines the SHARP market prices as +EV "
                        "(sharp_prob × payout > 1) — independent of the model:\n" + "\n".join(lines) + rec),
        "color": 0x8E44AD,
        "footer": {"text": "market vs Sleeper · pre-game sharp odds · paper-only"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("sleeper_arb_post_failed", error=str(e)[:120])


def run(post: bool = True, run_date: date | None = None):
    configure_logging()
    if run_date is None:
        run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    market = build_market_probs(run_date)          # LIVE fetch — pre-game, leak-free
    if not market:
        log.info("sleeper_arb_skipped", reason="no_market_data")
        return
    sharp_by_ps = _sharp_by_player_stat(market)
    with session_scope() as s:
        lines = _load_sleeper_lines(s, run_date)
        findings = compute_arb(lines, sharp_by_ps)
        _persist(s, run_date, findings)
    log.info("sleeper_arb_done", sleeper_lines=len(lines), matched_sharp=len(sharp_by_ps),
             plus_ev=len(findings), top=(findings[0]["ev"] if findings else None))
    if post:
        _post_discord(run_date, findings)


# ── forward realized-ROI tracker ─────────────────────────────────────────────
def arb_roi() -> dict:
    """Realized ROI of the persisted +EV arb picks, graded on-the-fly against
    settled outcomes. Forward-only (finder runs pre-game), played-only. Reuses the
    MIN_TIER_N gate so a thin sample reports 'building', not a phantom verdict."""
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT a.side, a.payout::float AS payout, a.line_value::float AS line,
                   (pg.stats->>a.stat_type)::float AS actual
            FROM sleeper_arb a
            JOIN player_games pg ON pg.player_id=a.player_id AND pg.game_id=a.game_id
            WHERE pg.stats ? a.stat_type AND COALESCE(pg.did_play, true)
        """)).mappings().all()
    rets = []
    for r in rows:
        over_win = r["actual"] > r["line"]
        win = over_win if r["side"] == "over" else not over_win
        rets.append((r["payout"] - 1.0) if win else -1.0)
    n = len(rets)
    if n == 0:
        return {"n": 0, "roi": 0.0, "lo": 0.0, "hi": 0.0, "hit": 0.0, "verdict": "—"}
    roi = sum(rets) / n
    se = (sum((x - roi) ** 2 for x in rets) / n / n) ** 0.5 if n > 1 else 0.0
    lo, hi = roi - 1.96 * se, roi + 1.96 * se
    hit = sum(1 for r, ret in zip(rows, rets) if ret > 0) / n
    verdict = ("building" if n < MIN_TIER_N else "profitable" if lo > 0
               else "losing" if hi < 0 else "not proven")
    return {"n": n, "roi": roi, "lo": lo, "hi": hi, "hit": hit, "verdict": verdict}


def _print_roi():
    s = arb_roi()
    print(f"Sleeper ARBITRAGE +EV picks (settled): n={s['n']}")
    if s["n"] >= MIN_TIER_N:
        print(f"  realized ROI: {s['roi']:+.1%}  [{s['lo']:+.1%}, {s['hi']:+.1%}]  "
              f"(hit {s['hit']:.1%})  →  {s['verdict']}")
    elif s["n"]:
        print(f"  {s['verdict']} ({s['n']}/{MIN_TIER_N} settled +EV picks) — ROI held back until the tier fills")
    else:
        print("  no settled arb picks yet — the forward track record starts as picks settle.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--roi", action="store_true", help="print the forward realized-ROI report")
    p.add_argument("--no-post", action="store_true")
    p.add_argument("--date", default=None, help="run for a specific date (YYYY-MM-DD)")
    args = p.parse_args()
    if args.roi:
        configure_logging()
        _print_roi()
        return
    rd = date.fromisoformat(args.date) if args.date else None
    run(post=not args.no_post, run_date=rd)


if __name__ == "__main__":
    main()
