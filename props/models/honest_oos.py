"""Point-in-time, out-of-sample evaluation of the recommended-tier win rate.

WHY THIS EXISTS
---------------
The headline "recommended-tier ~72%" is a *measurement* artifact, not a forward
expectation. `category_cutoffs.compute_from_db` selects each cutoff to maximise a
Wilson-lower-bound win rate on the settled-pick history, and then the reported
`win_rate` is measured on **that same history**. `daily_backtest` compounds it by
grading historical picks with `load_cutoffs()` — the *current* table, fit on data
that already includes the picks being graded. Either way the cutoff has seen the
outcomes it is scored against → selection on the dependent variable → the number
is inflated.

WHAT "HONEST" MEANS HERE (the hard rule)
----------------------------------------
Every pick is graded by the cutoff table that would have existed **at the moment
it was decided** — i.e. `compute_cutoffs` fit on ONLY the picks that had already
*settled* before that pick was made. No cutoff ever sees an outcome from the
window it is scored on. This reconstructs the system's real forward experience
(the live recommendation on day D used exactly the settlements available before
day D), so the number it reports is what the tier actually achieved going forward.

We deliberately REUSE `category_cutoffs.compute_cutoffs` / `rec_cutoff` — the exact
production selection logic — so the harness can't drift from what the system does;
only the *data each cutoff is allowed to see* is constrained.

Reports, per sport AND per (sport|stat|direction) category: recommended-tier n,
hit rate, Wilson 95% CI, and Brier, against the 57.7% per-leg parlay breakeven.
"""
from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict

from props.models.category_cutoffs import (
    BREAKEVEN, DEFAULT_CUTOFF, MIN_N_SPORT, compute_cutoffs, rec_cutoff,
)


# ── statistics ───────────────────────────────────────────────────────────────
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial rate (default 95%)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def brier(rows: list[dict]) -> float | None:
    """Mean squared error of the stored probability vs the 0/1 outcome."""
    if not rows:
        return None
    return sum((float(r["model_prob"]) - r["win"]) ** 2 for r in rows) / len(rows)


# ── the point-in-time replay ─────────────────────────────────────────────────
def walk_forward_oos(picks: list[dict], min_train: int = MIN_N_SPORT) -> list[dict]:
    """Replay picks in decision order; grade each with a cutoff table fit ONLY on
    strictly-prior settlements. Returns the picks that were recommended (the OOS
    recommended tier), each annotated with the cutoff it faced.

    Each pick needs: sport, stat_type, direction, model_prob, win (0/1),
    decided (comparable decision key, e.g. a date), settled (comparable settle
    key or None). A cutoff on decision-day D sees only picks with `settled < D`,
    so a pick can never inform the cutoff that judges it (it isn't settled yet at
    its own decision time, and same-day peers don't enter the same-day table).
    """
    # Group by decision key so we recompute the table once per day (production
    # recomputes once per morning) — same result as per-pick, far cheaper.
    by_decided: dict = defaultdict(list)
    for p in picks:
        by_decided[p["decided"]].append(p)

    # Settled picks, sorted by settle key, so we can grow the training frontier.
    settled_sorted = sorted(
        (p for p in picks if p["settled"] is not None),
        key=lambda p: p["settled"],
    )

    recommended: list[dict] = []
    frontier = 0                 # index into settled_sorted already in `train`
    train: list[dict] = []
    for decided in sorted(by_decided):
        # Advance the frontier: everything that settled STRICTLY BEFORE this
        # decision key is now known and may inform the cutoff.
        while frontier < len(settled_sorted) and settled_sorted[frontier]["settled"] < decided:
            train.append(settled_sorted[frontier])
            frontier += 1

        table = compute_cutoffs(train) if len(train) >= min_train else None
        for p in by_decided[decided]:
            if table is None:
                cutoff = DEFAULT_CUTOFF        # no history yet → production default
            else:
                cutoff = rec_cutoff(p["sport"], p["stat_type"], table, p["direction"])
            if float(p["model_prob"]) >= cutoff:
                recommended.append({**p, "cutoff": cutoff})
    return recommended


def leaky_insample_recommended(picks: list[dict]) -> list[dict]:
    """The BROKEN method, for contrast: fit ONE cutoff table on ALL settled picks,
    then recommend every pick against it — the cutoff has seen every outcome it is
    then scored on. This reproduces the inflated headline."""
    settled = [p for p in picks if p["settled"] is not None]
    table = compute_cutoffs(settled)
    out = []
    for p in picks:
        cutoff = rec_cutoff(p["sport"], p["stat_type"], table, p["direction"])
        if float(p["model_prob"]) >= cutoff:
            out.append(p)
    return out


# ── reporting ────────────────────────────────────────────────────────────────
def _summ(rows: list[dict]) -> dict:
    n = len(rows)
    k = sum(r["win"] for r in rows)
    lo, hi = wilson_ci(k, n)
    return {"n": n, "wins": k, "hit": (k / n if n else 0.0),
            "lo": lo, "hi": hi, "brier": brier(rows)}


def _verdict(s: dict) -> str:
    if s["n"] == 0:
        return "—"
    if s["lo"] >= BREAKEVEN:
        return "EDGE (95% CI clears breakeven)"
    if s["hi"] < BREAKEVEN:
        return "below breakeven"
    return "not proven (CI straddles breakeven)"


def report(recommended: list[dict], label: str = "OUT-OF-SAMPLE") -> dict:
    """Print + return the per-sport and per-category recommended-tier table."""
    be = BREAKEVEN
    overall = _summ(recommended)
    by_sport = {s: _summ(rs) for s, rs in _group(recommended, lambda p: p["sport"]).items()}
    by_cat = {c: _summ(rs) for c, rs in _group(
        recommended, lambda p: f"{p['sport']}|{p['stat_type']}|{p['direction']}").items()}

    print(f"\n=== {label} recommended-tier win rate  (breakeven = {be:.1%}) ===")
    print(f"{'':22} {'n':>5} {'hit':>7} {'Wilson 95% CI':>16} {'Brier':>7}  verdict")
    _row("ALL (blended)", overall)
    print("  — by sport —")
    for s in sorted(by_sport, key=lambda s: -by_sport[s]["n"]):
        _row(s, by_sport[s])
    print("  — by category (sport|stat|direction), n>=10 —")
    for c in sorted(by_cat, key=lambda c: -by_cat[c]["n"]):
        if by_cat[c]["n"] >= 10:
            _row(c, by_cat[c])
    return {"overall": overall, "by_sport": by_sport, "by_cat": by_cat}


def _row(name: str, s: dict) -> None:
    if s["n"] == 0:
        print(f"{name:22} {0:>5}      —                    —        —")
        return
    ci = f"[{s['lo']:.1%}, {s['hi']:.1%}]"
    br = f"{s['brier']:.3f}" if s["brier"] is not None else "—"
    print(f"{name:22} {s['n']:>5} {s['hit']:>7.1%} {ci:>16} {br:>7}  {_verdict(s)}")


def _group(rows, key):
    g: dict = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return g


# ── synthetic self-test ──────────────────────────────────────────────────────
def _synth(kind: str, n: int = 4500, days: int = 140, seed: int = 7) -> list[dict]:
    """Generate labelled synthetic picks with decision/settle days.

    noise  : win ⟂ model_prob (true rate 0.5). Honest harness must report ~50%.
    leaky  : same noise — used to show the OLD method inflates while honest ~50%.
    signal : model_prob is genuinely predictive (calibrated) → honest > breakeven.
    """
    rng = random.Random(seed)
    sports = ["mlb", "wnba", "nba", "nhl", "cfb", "soccer"]
    stats = ["a", "b", "c", "d"]
    dirs = ["over", "under"]
    picks = []
    for _ in range(n):
        prob = round(rng.uniform(0.55, 0.85), 3)
        if kind == "signal":
            p_true = prob                      # perfectly calibrated real edge
        else:
            p_true = 0.5                       # noise / leaky: no relationship
        win = 1 if rng.random() < p_true else 0
        d = rng.randint(0, days)
        picks.append({
            "sport": rng.choice(sports), "stat_type": rng.choice(stats),
            "direction": rng.choice(dirs), "model_prob": prob, "win": win,
            "decided": d, "settled": d + rng.randint(0, 2),  # settles 0-2 days later
        })
    return picks


def selftest() -> int:
    print("HONEST-OOS SELF-TEST  (breakeven = %.1f%%)" % (BREAKEVEN * 100))
    print("Reusing the real category_cutoffs selection logic; only the data each "
          "cutoff may see is constrained.\n")
    ok = True

    def honest_rate(picks):
        rec = walk_forward_oos(picks)
        return _summ(rec)["hit"], len(rec)

    def leaky_rate(picks):
        rec = leaky_insample_recommended(picks)
        return _summ(rec)["hit"], len(rec)

    # 1. NOISE: honest must land ~50%.
    noise = _synth("noise", seed=1)
    h, hn = honest_rate(noise)
    print(f"noise   honest    = {h:.1%}  (n_rec={hn})   expect ~50%")
    ok &= abs(h - 0.5) < 0.04

    # 2. LEAKY: the OLD in-sample method inflates > breakeven on the SAME kind of
    #    noise, while the honest harness stays ~50%. This is the exact bug + fix.
    leaky = _synth("leaky", seed=2)
    lh, ln = honest_rate(leaky)
    li, lin = leaky_rate(leaky)
    print(f"leaky   in-sample = {li:.1%}  (n_rec={lin})   <- BROKEN method inflates")
    print(f"leaky   honest    = {lh:.1%}  (n_rec={ln})   expect ~50%")
    ok &= abs(lh - 0.5) < 0.04
    ok &= li > BREAKEVEN            # the broken method must visibly over-report

    # 3. SIGNAL: a real calibrated edge → honest must clear breakeven.
    sig = _synth("signal", seed=3)
    sh, sn = honest_rate(sig)
    print(f"signal  honest    = {sh:.1%}  (n_rec={sn})   expect > {BREAKEVEN:.1%}")
    ok &= sh > BREAKEVEN

    print("\nSELF-TEST:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


# ── prod ─────────────────────────────────────────────────────────────────────
def load_prod_picks() -> list[dict]:
    """Settled picks eligible for an honest forward evaluation.

    FORWARD-ONLY at the source: we exclude every pick logged at/after its game
    started (``picked_at >= game_datetime``). Those are lookahead — the outcome
    was already partly/fully known when the pick was written (a bulk backfill of
    already-played games did this to ~11% of the ledger, and it single-handedly
    manufactured the mlb|hits|under "85.9%"). Filtering here — before the
    walk-forward selects anything — means a lookahead pick can never enter the
    pool a cutoff is fit on OR the tier it is scored in. This is what moves the
    honest blended rate from 56.4% (lookahead-inflated) to ~50.3% (clean).
    """
    from sqlalchemy import text
    from props.utils.db import engine, db_banner
    print(db_banner())
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT g.sport_code AS sport, pk.stat_type, pk.direction,
                   pk.model_prob, pk.leg_result,
                   (pk.picked_at  AT TIME ZONE 'America/Los_Angeles')::date AS decided,
                   (pk.settled_at AT TIME ZONE 'America/Los_Angeles')::date AS settled
            FROM picks pk JOIN games g USING (game_id)
            WHERE pk.leg_result IN ('win','loss') AND pk.model_prob IS NOT NULL
              AND g.game_datetime IS NOT NULL
              AND pk.picked_at < g.game_datetime   -- forward-only: no lookahead
        """)).mappings().all()
    out = []
    for r in rows:
        out.append({
            "sport": r["sport"], "stat_type": r["stat_type"],
            "direction": r["direction"], "model_prob": float(r["model_prob"]),
            "win": 1 if r["leg_result"] == "win" else 0,
            "decided": r["decided"], "settled": r["settled"],
        })
    return out


def run_prod() -> int:
    picks = load_prod_picks()
    settled = [p for p in picks if p["settled"] is not None]
    print(f"loaded {len(picks)} settled picks "
          f"({sum(p['win'] for p in picks)}W / {len(picks) - sum(p['win'] for p in picks)}L, "
          f"all-picks hit {sum(p['win'] for p in picks)/len(picks):.1%})")

    leaky = leaky_insample_recommended(picks)
    honest = walk_forward_oos(picks)
    ls = _summ(leaky)
    hs = _summ(honest)
    print(f"\nLEAKY  (in-sample cutoffs, the old headline): "
          f"{ls['hit']:.1%}  n={ls['n']}   <-- inflated")
    print(f"HONEST (point-in-time, forward):              "
          f"{hs['hit']:.1%}  n={hs['n']}   <-- the real number")
    report(honest, "HONEST OUT-OF-SAMPLE")
    _ = settled
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                    help="run synthetic noise/leaky/signal validation")
    args = ap.parse_args()
    return selftest() if args.selftest else run_prod()


if __name__ == "__main__":
    raise SystemExit(main())
