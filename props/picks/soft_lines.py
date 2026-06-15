"""Soft-line finder — PrizePicks lines vs the sharp market.

PrizePicks posts fixed lines; the sharp books (DraftKings/FanDuel) price the
*true* probability. Where a PrizePicks line is softer than the sharp consensus,
that's a +EV edge **independent of our model** — the classic prop-betting edge.

PrizePicks and the sharp books often post different *numbers* for the same
player, so we can't just read the sharp prob off. Instead we recover the sharp
market's implied **Poisson mean** from its no-vig over-prob at its own line, then
recompute P(over) at the *PrizePicks* line. A PrizePicks leg needs ≈57.7% to
clear a 2-pick power play, so a side whose market-implied win prob exceeds that
is a soft line; the gap is the edge.

Persists the day's soft lines (migration 0009) and posts a Discord digest of the
strongest. Read-only against the model — this is pure market vs PrizePicks.

Run:  python -m props.picks.soft_lines
"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from scipy.stats import poisson
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.config import settings
from props.utils.logging import log, configure_logging
from props.ingest.market_odds import build_market_probs

BREAKEVEN = 0.577
MIN_EDGE = 0.02            # only surface lines clearing breakeven with margin
LAMBDA_LO, LAMBDA_HI = 0.01, 250.0
# A reliable sharp anchor is a liquid MAIN line (no-vig prob near 0.5). A prob
# outside this band is almost always an alt line or a thin/mispriced market —
# not a trustworthy mean, so we skip it rather than chase a phantom 95% edge.
ANCHOR_MIN, ANCHOR_MAX = 0.20, 0.80


def implied_lambda(line: float, over_prob: float) -> float | None:
    """Poisson mean λ such that P(X > line) == over_prob. line is x.5, so
    P(over) = P(X ≥ ceil(line)) = sf(floor(line)). Monotonic in λ → bisect."""
    if over_prob is None or over_prob <= 0.0 or over_prob >= 1.0:
        return None
    k = int(line)                                  # floor of an x.5 line
    lo, hi = LAMBDA_LO, LAMBDA_HI
    # poisson.sf(k, λ) increases with λ; find λ matching over_prob.
    if poisson.sf(k, hi) < over_prob:              # even huge λ can't reach it
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        if poisson.sf(k, mid) < over_prob:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def p_over_at(line: float, lam: float) -> float:
    return float(poisson.sf(int(line), lam))


def _load_pp_props(session, run_date):
    """Today's PrizePicks STANDARD prop lines with player/game context."""
    return session.execute(text("""
        SELECT DISTINCT ON (pl.player_id, pl.stat_type, pl.line_value)
               pl.player_id, p.full_name AS player_name, pl.game_id,
               g.sport_code, pl.stat_type, pl.line_value::float AS pp_line
        FROM prop_lines pl
        JOIN players p USING (player_id)
        JOIN games g  ON g.game_id = pl.game_id
        WHERE pl.line_variant = 'standard'
          AND g.game_date = :d
        ORDER BY pl.player_id, pl.stat_type, pl.line_value, pl.snapshot_at DESC
    """), {"d": run_date}).all()


def _sharp_by_player_stat(market: dict) -> dict:
    """Group build_market_probs {(name,stat,line):over_prob} by (name,stat) ->
    list of (line, over_prob), so we can pick the reference sharp line."""
    out: dict = {}
    for (name, stat, line), prob in market.items():
        out.setdefault((name, stat), []).append((float(line), float(prob)))
    return out


def compute_soft_lines(pp_props, sharp_by_ps) -> list[dict]:
    findings = []
    for r in pp_props:
        key = (r.player_name.lower().strip(), r.stat_type)
        sharp = sharp_by_ps.get(key)
        if not sharp:
            continue
        # Anchor on the sharp MAIN line (no-vig prob nearest 0.5) — the most
        # liquid, least-extrapolated read of the player's true mean. Skip when
        # even that is implausibly lopsided (alt line / thin market).
        sharp_line, sharp_prob = min(sharp, key=lambda lp: abs(lp[1] - 0.5))
        if not (ANCHOR_MIN <= sharp_prob <= ANCHOR_MAX):
            continue
        lam = implied_lambda(sharp_line, sharp_prob)
        if lam is None:
            continue
        p_over = p_over_at(r.pp_line, lam)
        best_side = "over" if p_over >= 0.5 else "under"
        best_prob = max(p_over, 1.0 - p_over)
        edge = best_prob - BREAKEVEN
        findings.append({
            "sport_code": r.sport_code, "player_name": r.player_name,
            "stat_type": r.stat_type, "pp_line": round(r.pp_line, 2),
            "sharp_line": round(sharp_line, 2), "sharp_over_prob": round(sharp_prob, 4),
            "best_side": best_side, "best_prob": round(best_prob, 4),
            "edge": round(edge, 4), "game_id": r.game_id,
        })
    findings.sort(key=lambda f: f["edge"], reverse=True)
    return findings


def _persist(session, run_date, findings):
    session.execute(text("DELETE FROM soft_lines WHERE run_date = :d"), {"d": run_date})
    for f in findings:
        session.execute(text("""
            INSERT INTO soft_lines (run_date, sport_code, player_name, stat_type,
                pp_line, sharp_line, sharp_over_prob, best_side, best_prob, edge, game_id)
            VALUES (:d, :sport, :pn, :st, :ppl, :sl, :sop, :bs, :bp, :edge, :gid)
            ON CONFLICT (run_date, player_name, stat_type, pp_line) DO UPDATE SET
                sharp_line=EXCLUDED.sharp_line, sharp_over_prob=EXCLUDED.sharp_over_prob,
                best_side=EXCLUDED.best_side, best_prob=EXCLUDED.best_prob,
                edge=EXCLUDED.edge, created_at=NOW()
        """), {"d": run_date, "sport": f["sport_code"], "pn": f["player_name"],
               "st": f["stat_type"], "ppl": f["pp_line"], "sl": f["sharp_line"],
               "sop": f["sharp_over_prob"], "bs": f["best_side"], "bp": f["best_prob"],
               "edge": f["edge"], "gid": f["game_id"]})


def _post_discord(run_date, soft):
    if not settings.discord_webhook_url or not soft:
        return
    lines = []
    for f in soft[:8]:
        lines.append(f"`{f['best_side'].upper()} {f['pp_line']:g} {f['stat_type']}` "
                     f"**{f['player_name']}** — market {f['best_prob']:.0%} "
                     f"(sharp {f['sharp_line']:g}) · +{f['edge']*100:.0f}%")
    payload = {"embeds": [{
        "title": f"💰 Soft lines vs sharp market — {run_date:%a %b %-d}",
        "description": "PrizePicks lines the sharp market prices as +EV "
                       "(market-implied win % > 57.7% breakeven):\n" + "\n".join(lines),
        "color": 0x2ecc71,
        "footer": {"text": "market vs PrizePicks · independent of the model · paper-only"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("soft_lines_post_failed", error=str(e)[:120])


def run(post: bool = True):
    configure_logging()
    run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    market = build_market_probs(run_date)
    if not market:
        log.info("soft_lines_skipped", reason="no_market_data")
        return
    sharp_by_ps = _sharp_by_player_stat(market)
    with session_scope() as s:
        pp_props = _load_pp_props(s, run_date)
        findings = compute_soft_lines(pp_props, sharp_by_ps)
        _persist(s, run_date, findings)
    soft = [f for f in findings if f["edge"] >= MIN_EDGE]
    log.info("soft_lines_done", matched=len(findings), soft=len(soft),
             top=(soft[0]["edge"] if soft else None))
    if post:
        _post_discord(run_date, soft)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-post", action="store_true")
    args = p.parse_args()
    run(post=not args.no_post)


if __name__ == "__main__":
    main()
