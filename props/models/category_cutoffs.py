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


def _best_cutoff(probs, wins, min_n: int):
    """Lowest grid cutoff whose Wilson-LB win rate clears breakeven.

    `probs` and `wins` are equal-length sequences (model_prob, 0/1 win) for one
    category. Returns (cutoff, n, win_rate, wilson_lb) or None if nothing
    qualifies / not enough data overall.
    """
    pairs = list(zip(probs, wins))
    if len(pairs) < min_n:
        return None
    for t in GRID:
        sel = [w for p, w in pairs if p >= t]
        n = len(sel)
        if n < min_n:
            continue
        k = sum(sel)
        lb = wilson_lower_bound(k, n)
        if lb >= BREAKEVEN:
            return (t, n, k / n, lb)
    return None


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
        res = _best_cutoff(probs, wins, MIN_N_STAT)
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
    """Load the committed cutoff table, with a safe default if it's missing."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
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
