"""Per-pick-odds tracking for an ODDS book (Sleeper), the honest counterpart to
honest_oos for a flat pick'em (PrizePicks).

On PrizePicks every leg shared one 3x-parlay structure, so one breakeven (57.7%)
applied and the "recommended tier" was a fitted confidence cutoff. Sleeper posts
REAL per-pick odds (a multiplier per side, e.g. over 1.54x / under 2.10x), which
range 1.15x-3.53x — so a flat breakeven is meaningless. The honest measure is EV
against the actual price:

    a pick is +EV  iff  calibrate(model_prob) * payout > 1   (calibrate = the live
        Platt fit; raw model_prob runs ~12pts over-confident, so raw prob*payout>1
        floods the tier with false-+EV picks that lose — the -23% ROI lesson)
    realized return per pick = payout-1 if it wins, else -1
    ROI = mean realized return over the +EV picks

This stays forward-safe: the +EV decision uses the odds available at pick time and
a calibration fit only on picks that settled BEFORE it (self-tuning, like the
per-category cutoffs) — a pick's own outcome never feeds its own decision, so
there's no cutoff selected on the scored window. ROI>0 (CI floor above 0) means
the model beat the book's price after honest calibration — the only thing that
actually makes money. (calibrate() lives in the loader; pick_ev stays a pure
formula so the synthetic self-test can pass it known-true probs.)
"""
from __future__ import annotations

import argparse
import math
import random


def pick_ev(model_prob: float, payout: float) -> float:
    """Expected value per 1u stake: model_prob*payout - 1. >0 means +EV."""
    return model_prob * payout - 1.0


def roi_summary(picks: list[dict]) -> dict:
    """picks: dicts with model_prob, payout, win (0/1). Report the +EV tier's
    realized ROI (mean return per pick) with a normal-approx 95% CI."""
    ev_pos = [p for p in picks if pick_ev(float(p["model_prob"]), float(p["payout"])) > 0]
    n = len(ev_pos)
    if n == 0:
        return {"n_all": len(picks), "n": 0, "roi": 0.0, "lo": 0.0, "hi": 0.0,
                "hit": 0.0, "avg_payout": 0.0}
    rets = [(float(p["payout"]) - 1.0) if p["win"] else -1.0 for p in ev_pos]
    roi = sum(rets) / n
    var = sum((r - roi) ** 2 for r in rets) / n
    se = math.sqrt(var / n) if n > 1 else 0.0
    return {"n_all": len(picks), "n": n, "roi": roi,
            "lo": roi - 1.96 * se, "hi": roi + 1.96 * se,
            "hit": sum(1 for p in ev_pos if p["win"]) / n,
            "avg_payout": sum(float(p["payout"]) for p in ev_pos) / n}


# Below this many settled +EV picks the normal-approx CI isn't trustworthy (and
# on an all-win/all-loss run its width collapses to a false-certain 0), so we
# refuse a directional verdict and say "building" — symmetric, suppresses a
# spurious PROFITABLE just as readily as a spurious losing.
MIN_TIER_N = 30


def _verdict(s: dict) -> str:
    if s["n"] == 0:
        return "—"
    if s["n"] < MIN_TIER_N:
        return f"building ({s['n']}/{MIN_TIER_N} +EV picks)"
    if s["lo"] > 0:
        return "PROFITABLE (95% CI above 0)"
    if s["hi"] < 0:
        return "losing"
    return "not proven (CI straddles 0)"


# ── synthetic self-test ───────────────────────────────────────────────────────
def _synth(kind: str, n: int = 6000, seed: int = 7) -> list[dict]:
    """kind: noise (model ⟂ outcome) / signal-soft (calibrated + book overpays) /
    signal-sharp (calibrated + book vigs). p_true is the real win prob; the book's
    payout is set relative to fair (1/p_true)."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p_true = rng.uniform(0.35, 0.65)
        fair = 1.0 / p_true
        if kind == "signal-soft":
            payout = fair * 1.10          # book overpays 10% -> real edge exists
            model_prob = p_true
        elif kind == "signal-sharp":
            payout = fair * 0.92          # 8% vig -> no edge for a fair model
            model_prob = p_true
        else:                             # noise: random model, fair book
            payout = fair
            model_prob = rng.uniform(0.35, 0.65)
        out.append({"model_prob": model_prob, "payout": round(payout, 3),
                    "win": 1 if rng.random() < p_true else 0})
    return out


def selftest() -> int:
    print("ODDS-TRACK SELF-TEST  (+EV iff model_prob*payout>1; metric = realized ROI)\n")
    ok = True
    noise = roi_summary(_synth("noise", seed=1))
    soft = roi_summary(_synth("signal-soft", seed=2))
    sharp = roi_summary(_synth("signal-sharp", seed=3))
    print(f"noise        (random model, fair book):  ROI {noise['roi']:+.1%}  n={noise['n']}   expect ~0 / <0")
    print(f"signal-soft  (calibrated, book overpays): ROI {soft['roi']:+.1%}  n={soft['n']}   expect >0  [{soft['lo']:+.1%},{soft['hi']:+.1%}]")
    print(f"signal-sharp (calibrated, book vigs):     ROI {sharp['roi']:+.1%}  n={sharp['n']}   expect <=~0")
    ok &= noise["roi"] < 0.02          # noise must NOT manufacture profit
    ok &= soft["lo"] > 0               # a real soft edge is found (CI floor > 0)
    ok &= sharp["roi"] < 0.02          # a sharp/vig book yields no edge
    print("\nSELF-TEST:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


# ── prod ─────────────────────────────────────────────────────────────────────
def load_sleeper_picks() -> list[dict]:
    """Settled Sleeper picks joined to their line's odds — the payout for the side
    the pick took. Forward-only + valid-line + played, same source gates as
    honest_oos (a DNP is a void, a no-line pick isn't a bet)."""
    from sqlalchemy import text
    from props.utils.db import engine, db_banner
    print(db_banner())
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT g.sport_code AS sport, pk.stat_type, pk.direction, pk.model_prob,
                   CASE WHEN pk.direction='over' THEN pl.over_payout ELSE pl.under_payout END AS payout,
                   (pk.leg_result='win')::int AS win
            FROM picks pk
            JOIN games g USING (game_id)
            JOIN prop_lines pl ON pl.line_id = pk.line_id
            LEFT JOIN player_games pgame ON pgame.player_id = pk.player_id AND pgame.game_id = pk.game_id
            WHERE pl.sportsbook='sleeper' AND pk.leg_result IN ('win','loss')
              AND pk.model_prob IS NOT NULL AND g.game_datetime IS NOT NULL
              AND pk.picked_at < g.game_datetime
              AND CASE WHEN pk.direction='over' THEN pl.over_payout ELSE pl.under_payout END IS NOT NULL
              AND COALESCE(pgame.did_play, true)
        """)).mappings().all()
    # Judge +EV on the CALIBRATED prob — raw model_prob runs ~12pts over-confident,
    # so raw prob*payout>1 floods the tier with false-+EV picks (the -23% lesson).
    # Same live Platt fit the board's recommend flag now uses; selftest feeds true
    # probs straight to roi_summary, so it stays unaffected.
    from props.models.prob_calibration import calibrate
    return [{"sport": r["sport"], "stat_type": r["stat_type"], "direction": r["direction"],
             "model_prob": calibrate(float(r["model_prob"])), "payout": float(r["payout"]),
             "win": int(r["win"])} for r in rows]


def run_prod() -> int:
    picks = load_sleeper_picks()
    s = roi_summary(picks)
    print(f"\nSleeper settled picks: {len(picks)}  |  +EV tier: {s['n']}")
    if s["n"] >= MIN_TIER_N:
        print(f"  realized ROI: {s['roi']:+.1%}  [{s['lo']:+.1%}, {s['hi']:+.1%}]  "
              f"(hit {s['hit']:.1%} @ avg {s['avg_payout']:.2f}x)  →  {_verdict(s)}")
    elif s["n"]:
        # too few to trust an ROI — show accumulation, not a phantom ±100%
        print(f"  {_verdict(s)} — hit {s['hit']:.1%} @ avg {s['avg_payout']:.2f}x so far "
              f"(ROI held back until the tier reaches {MIN_TIER_N})")
    else:
        print("  no settled +EV Sleeper picks yet — tracking begins as picks settle.")
    return 0


def discord_digest() -> int:
    """Post the live Sleeper ROI to Discord (the nightly number that actually
    moves, vs the frozen PrizePicks baseline). No-op if no webhook configured."""
    import requests
    from props.utils.config import settings
    if not settings.discord_webhook_url:
        return 0
    picks = load_sleeper_picks()
    s = roi_summary(picks)
    if s["n_all"] == 0:
        desc = ("Tracking just started on Sleeper — realized ROI of the +EV tier "
                "populates as tonight's picks settle.")
        color = 0x9A9AA8
    elif s["n"] < MIN_TIER_N:
        # too few settled +EV picks to trust an ROI (a 3-pick run can read ±100%) —
        # show the accumulation instead of broadcasting a phantom number.
        color = 0x9A9AA8
        desc = (f"**Building** — {s['n']}/{MIN_TIER_N} settled +EV picks "
                f"(of {s['n_all']} settled overall). Too few for an honest ROI verdict; "
                f"the number populates once the +EV tier fills.")
    else:
        color = 0x2ECC71 if s["lo"] > 0 else 0xE74C3C if s["hi"] < 0 else 0xF1C40F
        desc = (f"**+EV tier: ROI {s['roi']:+.1%}**  [{s['lo']:+.1%}, {s['hi']:+.1%}]  "
                f"over {s['n']} picks (of {s['n_all']} settled)\n"
                f"hit {s['hit']:.1%} @ avg {s['avg_payout']:.2f}x · {_verdict(s)}")
    payload = {"embeds": [{
        "title": "🎯 prop-edge — Sleeper ROI (live track record)",
        "description": desc, "color": color,
        "footer": {"text": "+EV iff calibrated_prob·payout>1 · ROI = money made per unit vs the book's odds · paper"},
    }]}
    try:
        r = requests.post(settings.discord_webhook_url, json=payload, timeout=10)
        print("sleeper_digest_sent", r.status_code)
    except Exception as e:
        print("sleeper_digest_failed", str(e)[:100])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--digest", action="store_true", help="post the ROI to Discord")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.digest:
        return discord_digest()
    return run_prod()


if __name__ == "__main__":
    raise SystemExit(main())
