"""Per-category recommended-confidence cutoffs.

A single global `model_prob` cutoff is wrong for two reasons at once: the MLB
model is broadly +EV (it clears the 2-pick breakeven even at the pick-generation
floor), while the NBA model is a coin-flip until very high confidence. A flat
0.70 therefore *under-uses* MLB and *over-recommends* NBA.

This module derives a cutoff per **sport** (and, where there's enough data, per
**sport×stat**) straight from settled pick history, picking the lowest
`model_prob` threshold whose Wilson lower-bound win rate still clears the parlay
breakeven. The result is written to ``category_cutoffs.json`` (committed) so the
dashboard reads a static artifact instead of recomputing against the DB on every
render. Re-run ``python -m props.models.category_cutoffs`` to refresh it as more
picks settle (it's safe to wire into the daily job).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# 2-pick 3x parlay: each leg pays decimal sqrt(3) ~= 1.732, so a leg must hit
# 1/1.732 = 57.7% to break even. This is the bar every category must clear.
BREAKEVEN = 0.577

# Grid of candidate cutoffs. Floor is the pick-generation threshold (picks are
# only logged at model_prob > 0.55); ceiling is the 0.97 sanity cap.
GRID = [round(0.55 + 0.025 * i, 3) for i in range(13)]  # 0.55 .. 0.85

# Minimum settled picks required before we trust a computed cutoff. 30 keeps a
# sport from qualifying on a lucky high-confidence sliver (e.g. NBA looked +EV
# on a 20-pick top bucket that a larger sample didn't reproduce).
MIN_N_SPORT = 30
MIN_N_STAT = 40

# Wilson z. 1.0 (~68% one-sided) penalises small samples without being so
# strict that no real edge ever qualifies.
WILSON_Z = 1.0

# Fallback when a category has too little history to judge (e.g. WNBA/NHL).
DEFAULT_CUTOFF = 0.70
# Cutoff that effectively suppresses a category from the recommended tier when
# its model never clears breakeven at any confidence level.
SUPPRESS_CUTOFF = 0.99

_JSON_PATH = Path(__file__).with_name("category_cutoffs.json")


def wilson_lower_bound(wins: int, n: int, z: float = WILSON_Z) -> float:
    """One-sided Wilson lower bound on a binomial win rate."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def wilson_upper_bound(wins: int, n: int, z: float = WILSON_Z) -> float:
    """One-sided Wilson upper bound on a binomial win rate."""
    if n == 0:
        return 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre + margin) / denom


# How much higher a cutoff's Wilson-LB win rate must be to justify stepping up
# from a lower (higher-volume) cutoff that already clears breakeven. A flat 0.55
# floor maximises slate size but ignores that a higher cutoff can be markedly
# safer — and on a 2-pick parlay per-leg win rate compounds (0.70 → 49% joint,
# 0.84 → 71% joint), so a materially safer leg is worth the lost volume.
LB_STEPUP_MARGIN = 0.04


def _best_cutoff(probs, wins, min_n: int, stepup: bool = False):
    """Best grid cutoff among those whose Wilson-LB win rate clears breakeven.

    Every candidate must clear the 57.7% breakeven on its conservative
    (Wilson lower-bound) win rate. Among those, we take the *lowest* cutoff —
    i.e. the most volume.

    When ``stepup`` is set (used for per-stat cutoffs, where the sample is
    focused) we step UP to a higher cutoff if it is meaningfully safer
    (Wilson-LB higher by > LB_STEPUP_MARGIN), returning the lowest cutoff that
    reaches within that margin of the safest one. This keeps volume when the
    floor is already strong but captures a higher-quality slice when the model
    is much sharper at higher confidence (e.g. MLB hits: 70% @0.55 vs 84%
    @0.625). The coarse sport-level fallback stays permissive (stepup=False) so
    it doesn't over-suppress stats that inherit it. Returns
    (cutoff, n, win_rate, wilson_lb) or None.
    """
    pairs = list(zip(probs, wins))
    if len(pairs) < min_n:
        return None

    qualifying = []  # (cutoff, n, win_rate, lb) for cutoffs clearing breakeven
    for t in GRID:
        sel = [w for p, w in pairs if p >= t]
        n = len(sel)
        if n < min_n:
            continue
        k = sum(sel)
        lb = wilson_lower_bound(k, n)
        if lb >= BREAKEVEN:
            qualifying.append((t, n, k / n, lb))

    if not qualifying:
        return None

    if not stepup:
        return qualifying[0]                  # GRID ascending → lowest clearing

    best_lb = max(q[3] for q in qualifying)
    # Lowest cutoff whose Wilson-LB is within the step-up margin of the safest.
    for t, n, wr, lb in qualifying:           # GRID is ascending → lowest first
        if lb >= best_lb - LB_STEPUP_MARGIN:
            return (t, n, wr, lb)
    return qualifying[0]


def compute_cutoffs(rows) -> dict:
    """Build the cutoff table from settled-pick rows.

    `rows` is an iterable of dicts/objects with keys: sport, stat_type,
    model_prob, win (1/0). Returns a JSON-serialisable dict.
    """
    by_sport: dict[str, list] = {}
    by_stat: dict[tuple, list] = {}
    for r in rows:
        sport = r["sport"]
        stat = r["stat_type"]
        p = float(r["model_prob"])
        w = int(r["win"])
        by_sport.setdefault(sport, []).append((p, w))
        by_stat.setdefault((sport, stat), []).append((p, w))

    sports: dict[str, dict] = {}
    for sport, pairs in sorted(by_sport.items()):
        probs = [p for p, _ in pairs]
        wins = [w for _, w in pairs]
        res = _best_cutoff(probs, wins, MIN_N_SPORT)
        if res is None:
            # Enough data to judge but model never clears -> suppress;
            # too little data -> fall back to the conservative default.
            cutoff = SUPPRESS_CUTOFF if len(pairs) >= MIN_N_SPORT else DEFAULT_CUTOFF
            sports[sport] = {
                "cutoff": cutoff,
                "n": len(pairs),
                "win_rate": round(sum(wins) / len(pairs), 4) if pairs else None,
                "wilson_lb": None,
                "status": "suppressed" if cutoff == SUPPRESS_CUTOFF else "unproven",
            }
        else:
            t, n, wr, lb = res
            sports[sport] = {
                "cutoff": t,
                "n": n,
                "win_rate": round(wr, 4),
                "wilson_lb": round(lb, 4),
                "status": "tuned",
            }

    stats: dict[str, dict] = {}
    for (sport, stat), pairs in sorted(by_stat.items()):
        probs = [p for p, _ in pairs]
        wins = [w for _, w in pairs]
        n_all, k_all = len(pairs), sum(wins)
        res = _best_cutoff(probs, wins, MIN_N_STAT, stepup=True)
        if res is not None:
            t, n, wr, lb = res
            stats[f"{sport}|{stat}"] = {
                "cutoff": t, "n": n, "win_rate": round(wr, 4),
                "wilson_lb": round(lb, 4), "status": "tuned",
            }
            continue
        # No qualifying cutoff. If there's enough data AND the stat is
        # CONFIDENTLY below breakeven (Wilson upper bound < breakeven), suppress
        # it explicitly — otherwise it would silently inherit the more permissive
        # SPORT cutoff and keep getting recommended (e.g. NBA points: 57% at the
        # sport's 0.725, sub-breakeven). Without enough data, defer to the sport.
        if n_all >= MIN_N_STAT and wilson_upper_bound(k_all, n_all) < BREAKEVEN:
            stats[f"{sport}|{stat}"] = {
                "cutoff": SUPPRESS_CUTOFF, "n": n_all,
                "win_rate": round(k_all / n_all, 4),
                "wilson_ub": round(wilson_upper_bound(k_all, n_all), 4),
                "status": "suppressed",
            }

    return {
        "breakeven": BREAKEVEN,
        "default_cutoff": DEFAULT_CUTOFF,
        "sports": sports,
        "stats": stats,
    }


# ── runtime lookup (used by the dashboard) ───────────────────────────────────

_CACHE: dict | None = None


def load_cutoffs() -> dict:
    """Cutoff table for runtime use. Recomputes from the live DB so cutoffs
    self-tune as picks settle (no manual regen) — e.g. a suppressed stat lifts
    itself once it proves out. Falls back to the committed JSON if the DB is
    unreachable, then to safe defaults. Cached per-process."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        tbl = compute_from_db()
        if tbl and tbl.get("sports"):
            _CACHE = tbl
            return _CACHE
    except Exception:
        pass  # DB unreachable / empty — fall back to the committed seed
    try:
        _CACHE = json.loads(_JSON_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        _CACHE = {
            "breakeven": BREAKEVEN,
            "default_cutoff": DEFAULT_CUTOFF,
            "sports": {},
            "stats": {},
        }
    return _CACHE


def rec_cutoff(sport: str | None, stat_type: str | None = None,
               table: dict | None = None) -> float:
    """Recommended `model_prob` cutoff for a pick.

    Hierarchy: sport×stat (if tuned) -> sport (if tuned/suppressed) -> default.
    Pass `table` to use a freshly computed table (e.g. the dashboard's live,
    cached recompute); otherwise the committed JSON is used.
    """
    table = table if table is not None else load_cutoffs()
    sport = (sport or "").lower()
    if stat_type:
        cell = table.get("stats", {}).get(f"{sport}|{stat_type}")
        if cell:
            return float(cell["cutoff"])
    sp = table.get("sports", {}).get(sport)
    if sp:
        return float(sp["cutoff"])
    return float(table.get("default_cutoff", DEFAULT_CUTOFF))


# ── CLI: recompute from the DB and write the JSON ────────────────────────────

def _fetch_rows(engine=None):
    from sqlalchemy import text
    import pandas as pd
    if engine is None:
        from props.utils.db import engine as engine

    df = pd.read_sql(text("""
        SELECT g.sport_code AS sport, pk.stat_type,
               pk.model_prob, pk.leg_result
        FROM picks pk JOIN games g USING (game_id)
        WHERE pk.leg_result IN ('win', 'loss')
    """), engine)
    df["win"] = (df["leg_result"] == "win").astype(int)
    return df.to_dict("records")


def compute_from_db(engine=None) -> dict:
    """Recompute the cutoff table directly from settled picks in the DB.

    Used by the dashboard for a live (cached) refresh so cutoffs track new
    results without a redeploy. Falls back to the committed JSON on the caller's
    side if this raises.
    """
    return compute_cutoffs(_fetch_rows(engine))


def main():
    # The committed JSON is the dashboard's seed/fallback, so build it from the
    # SAME database the dashboard reads (prod Railway) when that's configured;
    # fall back to the default engine (local) otherwise.
    engine = None
    from props.utils.config import settings
    if settings.railway_database_url:
        from sqlalchemy import create_engine
        engine = create_engine(settings.railway_database_url)
        print("using RAILWAY DB (prod) for the committed seed")
    rows = _fetch_rows(engine)
    table = compute_cutoffs(rows)
    _JSON_PATH.write_text(json.dumps(table, indent=2) + "\n")
    print(f"wrote {_JSON_PATH}  ({len(rows)} settled picks)")
    for sport, info in table["sports"].items():
        print(f"  {sport:5s} cutoff={info['cutoff']:.3f}  "
              f"n={info['n']:4d}  wr={info['win_rate']}  "
              f"status={info['status']}")
    if table["stats"]:
        print("  stat-level overrides:")
        for key, info in table["stats"].items():
            print(f"    {key:28s} cutoff={info['cutoff']:.3f}  "
                  f"n={info['n']}  wr={info['win_rate']}")


if __name__ == "__main__":
    main()
