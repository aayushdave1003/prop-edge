"""Promo / odds-boost tracker — the one lane with a REAL, structural +EV.

Every modeling lane here measured too-small-vs-vig (predict, arbitrage, correlate).
Odds boosts are different: the book *deliberately* prices them +EV — a "boost of the
day" pays better-than-fair odds to acquire/retain you. So the edge isn't a model
being smarter than the market; it's the book *choosing* to overpay, and this tool
just tells you which boosts are worth taking.

A boost is +EV exactly when

    sharp_true_prob × boosted_decimal_odds  >  1

which is the SAME test as the arbitrage finder — only here the "payout" is the
boosted price the book advertised, and the true prob is the sharp no-vig consensus
(the pluggable SHARP_FEED reference: DK/FD default, or a paid feed if wired). Boosts
are usually on stars, whom the sharp market prices well, so most are evaluable.

Boosts are entered by hand (there's no clean promo API; scraping book promo pages is
Cloudflare-fragile) — a ~1-minute daily glance at DK/FD/etc. The value is the pricing,
not the scraping. Workflow:

  python -m props.picks.boost_ev --add --book DK --player "Aaron Judge" \
      --stat total_bases --line 1.5 --side over --odds 200   # adds + prints EV
  python -m props.picks.boost_ev --check --player "Aaron Judge" \
      --stat total_bases --line 1.5 --side over --odds 200    # instant, no persist
  python -m props.picks.boost_ev --digest      # evaluate today's boosts + post +EV ones
  python -m props.picks.boost_ev --roi         # forward realized ROI of the +EV boosts
  python -m props.picks.boost_ev --list

Honest guardrails (identical to the arb, so we never flag a phantom edge): the sharp
anchor must be a liquid main line (no-vig prob in [0.20, 0.80]) or the boost is marked
un-pricable, and the +EV boosts are graded forward against player_games so the tool
earns a realized track record instead of just asserting EV.
"""
import argparse
import os
import re
import sys
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from props.utils.db import session_scope, engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging
from props.ingest.sharp_feed import get_sharp_feed
from props.picks.sleeper_arb import _sharp_by_player_stat
from props.picks.soft_lines import implied_lambda, p_over_at
from props.models.odds_track import MIN_TIER_N

ANCHOR_MIN, ANCHOR_MAX = 0.20, 0.80


def american_to_decimal(a: int) -> float:
    """American odds -> decimal payout multiplier (+200 -> 3.0, -110 -> 1.909)."""
    a = int(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / (-a))


def _price_boost(player_name, stat_type, line, side, odds, sharp_by_ps) -> dict:
    """EV of one boost vs the sharp market. Always returns a dict; `ev`/`true_prob`
    are None when the boost can't be priced (with a human `note` explaining why)."""
    dec = american_to_decimal(odds)
    base = {"player_name": player_name, "stat_type": stat_type, "line": round(float(line), 2),
            "side": side, "odds": int(odds), "decimal": round(dec, 3),
            "true_prob": None, "ev": None, "note": ""}
    sharp = sharp_by_ps.get((player_name.lower().strip(), stat_type))
    if not sharp:
        base["note"] = "no sharp line for this player/stat — can't price"
        return base
    s_line, s_prob = min(sharp, key=lambda lp: abs(lp[1] - 0.5))   # sharp MAIN line
    if not (ANCHOR_MIN <= s_prob <= ANCHOR_MAX):
        base["note"] = f"sharp anchor unreliable ({s_prob:.0%}) — skipped"
        return base
    lam = implied_lambda(s_line, s_prob)
    if lam is None:
        base["note"] = "couldn't recover a Poisson mean from the sharp line"
        return base
    p_over = p_over_at(float(line), lam)
    true_prob = p_over if side == "over" else 1.0 - p_over
    base["true_prob"] = round(true_prob, 4)
    base["ev"] = round(true_prob * dec - 1.0, 4)
    base["sharp_line"] = round(s_line, 2)
    return base


def _resolve_player_id(session, name) -> int | None:
    row = session.execute(text(
        "SELECT player_id FROM players WHERE lower(full_name) = lower(:n) "
        "ORDER BY player_id LIMIT 1"), {"n": name.strip()}).first()
    return row[0] if row else None


def _run_date(d: str | None) -> date:
    if d:
        return date.fromisoformat(d)
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def _sharp_market(run_date) -> dict:
    """Group the active sharp feed's probs by (player, stat) for anchoring."""
    feed = get_sharp_feed()
    market = feed.build_probs(run_date)
    return _sharp_by_player_stat(market) if market else {}


def add_boost(run_date, book, player_name, stat_type, line, side, odds, note="",
              sharp_by_ps=None):
    """Persist a boost (evaluating it against today's sharp market) and return the
    priced result — so adding a boost immediately tells you whether to take it. Pass
    a precomputed ``sharp_by_ps`` to avoid a per-boost fetch in batch adds."""
    if sharp_by_ps is None:
        sharp_by_ps = _sharp_market(run_date)
    priced = _price_boost(player_name, stat_type, line, side, odds, sharp_by_ps)
    with session_scope() as s:
        pid = _resolve_player_id(s, player_name)
        s.execute(text("""
            INSERT INTO promo_boosts (run_date, book, player_id, player_name, stat_type,
                line_value, side, boosted_odds, true_prob, decimal_odds, ev, note)
            VALUES (:d, :bk, :pid, :pn, :st, :ln, :sd, :od, :tp, :dec, :ev, :nt)
            ON CONFLICT (run_date, book, player_name, stat_type, line_value, side) DO UPDATE SET
                boosted_odds=EXCLUDED.boosted_odds, true_prob=EXCLUDED.true_prob,
                decimal_odds=EXCLUDED.decimal_odds, ev=EXCLUDED.ev, note=EXCLUDED.note,
                player_id=EXCLUDED.player_id, created_at=NOW()
        """), {"d": run_date, "bk": book, "pid": pid, "pn": player_name, "st": stat_type,
               "ln": round(float(line), 2), "sd": side, "od": int(odds),
               "tp": priced["true_prob"], "dec": priced["decimal"], "ev": priced["ev"],
               "nt": note or priced["note"]})
    priced["book"] = book
    return priced


def add_many(run_date, raw_lines) -> list[dict]:
    """Batch-add boosts pasted one per line: `book, player, stat, line, side, odds[, note]`
    (comma OR pipe separated). Blank lines and `#` comments are skipped. One sharp fetch
    for the whole batch. Returns the priced boosts; prints each as it's added."""
    sharp_by_ps = _sharp_market(run_date)      # one fetch for the whole paste
    out = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[|,\t]", raw) if p.strip() != ""]
        if len(parts) < 6:
            print(f"  skip (need book,player,stat,line,side,odds): {raw!r}"); continue
        book, player, stat, line, side, odds = parts[:6]
        note = parts[6] if len(parts) > 6 else ""
        try:
            b = add_boost(run_date, book, player, stat, float(line), side.lower().strip(),
                          int(str(odds).replace("+", "")), note, sharp_by_ps=sharp_by_ps)
        except (ValueError, TypeError) as e:
            print(f"  skip ({e}): {raw!r}"); continue
        out.append(b)
        tag = ("  → TAKE ✅" if (b["ev"] or -1) > 0 else "  → pass" if b["ev"] is not None else "")
        print("  added: " + _fmt(b) + tag)
    plus = sum(1 for b in out if (b["ev"] or -1) > 0)
    print(f"\n{len(out)} boost(s) added · {plus} are +EV.")
    return out


def evaluate(run_date) -> list[dict]:
    """Re-price every stored boost for the date against the current sharp market and
    write true_prob/ev back. Returns all boosts (priced + un-pricable), EV desc."""
    out = []
    with session_scope() as s:
        rows = s.execute(text("""
            SELECT book, player_name, stat_type, line_value::float AS line, side,
                   boosted_odds, note FROM promo_boosts WHERE run_date = :d
        """), {"d": run_date}).mappings().all()
        if not rows:
            return []                       # no boosts entered → skip the live sharp fetch (quota)
        sharp_by_ps = _sharp_market(run_date)
        for r in rows:
            p = _price_boost(r["player_name"], r["stat_type"], r["line"], r["side"],
                             r["boosted_odds"], sharp_by_ps)
            p["book"] = r["book"]
            # preserve a human note the user typed; else use the pricing note
            keep = r["note"] if (r["note"] and not r["note"].startswith(("no sharp", "sharp anchor",
                                 "couldn't"))) else p["note"]
            s.execute(text("""
                UPDATE promo_boosts SET true_prob=:tp, decimal_odds=:dec, ev=:ev, note=:nt
                WHERE run_date=:d AND book=:bk AND player_name=:pn AND stat_type=:st
                  AND line_value=:ln AND side=:sd
            """), {"tp": p["true_prob"], "dec": p["decimal"], "ev": p["ev"], "nt": keep,
                   "d": run_date, "bk": r["book"], "pn": r["player_name"],
                   "st": r["stat_type"], "ln": r["line"], "sd": r["side"]})
            out.append(p)
    out.sort(key=lambda x: (x["ev"] is not None, x["ev"] or 0), reverse=True)
    return out


def _fmt(b: dict) -> str:
    od = f"+{b['odds']}" if b["odds"] > 0 else str(b["odds"])
    head = f"{b['side'].upper()} {b['line']:g} {b['stat_type']}"
    who = f"{b['player_name']} ({b['book']})"
    if b["ev"] is None:
        return f"`{head}` {who} — {b['note']}"
    return (f"`{head}` {who} — sharp {b['true_prob']:.0%} @ {od} ({b['decimal']:.2f}x) "
            f"· {b['ev']*100:+.0f}% EV")


def _nudge_payload(run_date, s: dict) -> dict:
    """The empty-day reminder embed (pure — no I/O, so it's testable). Doubles as a
    status line: appends the accruing track record if the tier has filled."""
    body = ("No boosts entered yet today. Odds boosts are the one **structural** +EV lane — "
            "a 1-minute glance at your books pays for itself. Drop them in:\n"
            "```\npython -m props.picks.boost_ev --add-many\n"
            "DK, Aaron Judge, total_bases, 1.5, over, +200\n```\n"
            "(or `--check` a single one). Each is priced vs the sharp market — take the +EV ones.")
    if s["n"] >= MIN_TIER_N:
        body += f"\n\n**Track record so far:** ROI {s['roi']:+.1%} over {s['n']} settled · {s['verdict']}"
    elif s["n"]:
        body += f"\n\n**Building:** {s['n']}/{MIN_TIER_N} settled +EV boosts."
    return {"embeds": [{
        "title": f"🎯 Promo boosts — {run_date:%a %b %-d} · none entered yet",
        "description": body,
        "color": 0xF39C12,
        "footer": {"text": "reminder · set BOOST_NUDGE=0 to silence · paper-only"},
    }]}


def _post_nudge(run_date):
    """Post the empty-day reminder (the user asked to be nudged). Silenceable via
    BOOST_NUDGE=0 — a daily prompt should always have an off switch."""
    if os.getenv("BOOST_NUDGE", "1") == "0":
        return
    try:
        requests.post(settings.discord_webhook_url,
                      json=_nudge_payload(run_date, boost_roi()), timeout=10)
    except Exception as e:
        log.warning("boost_ev_nudge_failed", error=str(e)[:120])


def discord_digest(run_date, post: bool = True) -> list[dict]:
    results = evaluate(run_date)
    plus = [b for b in results if b["ev"] is not None and b["ev"] > 0]
    log.info("boost_ev_done", boosts=len(results), plus_ev=len(plus),
             top=(plus[0]["ev"] if plus else None))
    if not post or not settings.discord_webhook_url:
        return results
    if not results:
        _post_nudge(run_date)      # empty day → gentle reminder, not a silent skip
        return results
    if plus:
        body = "**Take these — the book is overpaying vs the sharp market:**\n" + \
               "\n".join(_fmt(b) for b in plus[:10])
    else:
        body = "_No +EV boosts today (all priced at or below fair)._"
    skipped = [b for b in results if b["ev"] is None]
    if skipped:
        body += f"\n\n_{len(skipped)} boost(s) couldn't be priced (no sharp line)._"
    s = boost_roi()
    if s["n"] >= MIN_TIER_N:
        body += f"\n\n**Track record:** ROI {s['roi']:+.1%} [{s['lo']:+.1%}, {s['hi']:+.1%}] over {s['n']} settled · {s['verdict']}"
    elif s["n"]:
        body += f"\n\n**Track record:** building — {s['n']}/{MIN_TIER_N} settled +EV boosts"
    payload = {"embeds": [{
        "title": f"🎯 Promo boosts — {run_date:%a %b %-d}",
        "description": body,
        "color": 0x27AE60,
        "footer": {"text": "boosts are book-subsidized +EV · priced vs sharp market · paper-only"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("boost_ev_post_failed", error=str(e)[:120])
    return results


def boost_roi() -> dict:
    """Forward realized ROI of the boosts we flagged +EV (ev > 0), graded on-the-fly
    against settled player_games. Same MIN_TIER_N gate as every other lane so a thin
    sample reports 'building' rather than a phantom verdict."""
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT b.side, b.decimal_odds::float AS dec, b.line_value::float AS line,
                   (pg.stats->>b.stat_type)::float AS actual
            FROM promo_boosts b
            JOIN players p ON p.player_id = COALESCE(b.player_id,
                 (SELECT player_id FROM players WHERE lower(full_name)=lower(b.player_name) LIMIT 1))
            JOIN player_games pg ON pg.player_id = p.player_id
            JOIN games g ON g.game_id = pg.game_id AND g.game_date = b.run_date
            WHERE b.ev > 0 AND pg.stats ? b.stat_type AND COALESCE(pg.did_play, true)
        """)).mappings().all()
    rets = []
    for r in rows:
        over_win = r["actual"] > r["line"]
        win = over_win if r["side"] == "over" else not over_win
        rets.append((r["dec"] - 1.0) if win else -1.0)
    n = len(rets)
    if n == 0:
        return {"n": 0, "roi": 0.0, "lo": 0.0, "hi": 0.0, "hit": 0.0, "verdict": "—"}
    roi = sum(rets) / n
    se = (sum((x - roi) ** 2 for x in rets) / n / n) ** 0.5 if n > 1 else 0.0
    lo, hi = roi - 1.96 * se, roi + 1.96 * se
    hit = sum(1 for ret in rets if ret > 0) / n
    verdict = ("building" if n < MIN_TIER_N else "profitable" if lo > 0
               else "losing" if hi < 0 else "not proven")
    return {"n": n, "roi": roi, "lo": lo, "hi": hi, "hit": hit, "verdict": verdict}


def _list(run_date):
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT book, player_name, stat_type, line_value::float AS line, side,
                   boosted_odds AS odds, ev FROM promo_boosts
            WHERE run_date = :d ORDER BY ev DESC NULLS LAST
        """), {"d": run_date}).mappings().all()
    if not rows:
        print(f"No boosts entered for {run_date}."); return
    print(f"Boosts for {run_date}:")
    for r in rows:
        ev = f"{r['ev']*100:+.0f}% EV" if r["ev"] is not None else "unpriced"
        od = f"+{r['odds']}" if r["odds"] > 0 else str(r["odds"])
        print(f"  {r['side'].upper():5} {r['line']:g} {r['stat_type']:14} {r['player_name']:22} "
              f"({r['book']}) @ {od:>5}  {ev}")


def _print_roi():
    s = boost_roi()
    if s["n"] >= MIN_TIER_N:
        print(f"Promo boosts flagged +EV (settled): n={s['n']}  ROI {s['roi']:+.1%}  "
              f"[{s['lo']:+.1%}, {s['hi']:+.1%}]  hit {s['hit']:.1%}  →  {s['verdict']}")
    elif s["n"]:
        print(f"Promo boosts: building — {s['n']}/{MIN_TIER_N} settled +EV boosts "
              f"(ROI held until the tier fills)")
    else:
        print("Promo boosts: no settled +EV boosts yet.")


def _require(args, *names):
    missing = [n for n in names if getattr(args, n) is None]
    if missing:
        raise SystemExit(f"missing required: {', '.join('--' + m for m in missing)}")


def main():
    p = argparse.ArgumentParser(description="Promo / odds-boost EV tracker")
    p.add_argument("--add", action="store_true", help="add + persist a boost")
    p.add_argument("--add-many", dest="add_many", action="store_true",
                   help="batch-add boosts from --file or stdin: 'book,player,stat,line,side,odds' per line")
    p.add_argument("--file", default=None, help="file of boosts for --add-many (else reads stdin)")
    p.add_argument("--check", action="store_true", help="price a boost on-demand (no persist)")
    p.add_argument("--eval", action="store_true", help="re-price all of the date's boosts")
    p.add_argument("--digest", action="store_true", help="evaluate + post the +EV digest")
    p.add_argument("--roi", action="store_true", help="forward realized ROI of +EV boosts")
    p.add_argument("--list", action="store_true", help="list the date's boosts")
    p.add_argument("--book", default="")
    p.add_argument("--player")
    p.add_argument("--stat")
    p.add_argument("--line", type=float)
    p.add_argument("--side", choices=["over", "under"])
    p.add_argument("--odds", type=int, help="american odds, e.g. 200 or -110")
    p.add_argument("--note", default="")
    p.add_argument("--date", default=None)
    args = p.parse_args()
    configure_logging()
    rd = _run_date(args.date)

    if args.roi:
        _print_roi()
    elif args.list:
        _list(rd)
    elif args.check:
        _require(args, "player", "stat", "line", "side", "odds")
        b = _price_boost(args.player, args.stat, args.line, args.side, args.odds, _sharp_market(rd))
        b["book"] = args.book or "?"
        print(_fmt(b))
        if b["ev"] is not None:
            print("  →  " + ("TAKE IT ✅" if b["ev"] > 0 else "pass ❌"))
    elif args.add:
        _require(args, "player", "stat", "line", "side", "odds")
        b = add_boost(rd, args.book or "?", args.player, args.stat, args.line,
                      args.side, args.odds, args.note)
        print("added: " + _fmt(b))
        if b["ev"] is not None:
            print("  →  " + ("TAKE IT ✅" if b["ev"] > 0 else "pass ❌"))
    elif args.add_many:
        if args.file:
            with open(args.file) as fh:
                raw = fh.readlines()
        else:
            print("paste boosts (book,player,stat,line,side,odds per line), then Ctrl-D:")
            raw = sys.stdin.readlines()
        add_many(rd, raw)
    elif args.digest:
        discord_digest(rd, post=True)
    elif args.eval:
        for b in evaluate(rd):
            print(_fmt(b))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
