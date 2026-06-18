"""Build correlated parlay recommendations from today's picks.

PrizePicks pays fixed multipliers regardless of correlation between legs:
  2-pick: 3x  |  3-pick: 5x  |  4-pick: 10x  |  5-pick: 20x

Same-team/same-game overs on high-total games are positively correlated —
both hit more often together than independent probability suggests. PP
doesn't adjust its multiplier for this, making those combos structurally
underpriced relative to their true EV.

EV formula:  multiplier * P(all legs hit) - 1
A 2-pick at 3x with 65%/65% independent legs has EV = 3*0.4225 - 1 = +26.75%.
If those legs are correlated (same team overs), the true joint prob is higher,
so EV is even better than the independent calculation suggests.
"""
import itertools
import numpy as np
import pandas as pd
from scipy.stats import norm

MULTIPLIERS = {2: 3.0, 3: 5.0, 4: 10.0}

# ── Empirical leg-outcome correlations (rho of the "went over its line" indicator),
# measured from settled history (correlate per-player over-indicators = actual >
# last_10_avg, within games and per same-player stat pair). Replaces the old flat
# heuristics, which OVERSTATED cross-player (same-team 0.06 / diff-team 0.03 vs
# measured ~0.04 / ~0) and grossly UNDERSTATED same-player pairs (flat 0.08 vs
# measured 0.26-0.72). For two Bernoulli legs, Cov = rho * sqrt(pA(1-pA) pB(1-pB));
# both-over ≈ both-under ≈ rho, opposite over/under flips the sign.
RHO_SAME_TEAM           = 0.04   # same team, same direction — game offense lifts teammates a little
RHO_DIFF_TEAM           = 0.0    # different teams — empirically ~0 (the old 0.03 was spurious)
RHO_SAME_PLAYER_DEFAULT = 0.20   # any same-player pair not specifically measured below

# Same-player stat pairs → measured rho of their over-indicators.
NATURAL_PAIR_RHO = {
    frozenset(["hits", "total_bases"]):  0.72,
    frozenset(["total_bases", "rbis"]):  0.48,
    frozenset(["hits", "runs"]):         0.38,
    frozenset(["points", "rebounds"]):   0.34,
    frozenset(["rebounds", "assists"]):  0.27,
    frozenset(["points", "assists"]):    0.26,
}
# A combo stat strongly tracks any base stat it contains (e.g. points & PRA).
_COMBO_COMPONENTS = {
    "pts_rebs_asts":  {"points", "rebounds", "assists"},
    "pts_rebs":       {"points", "rebounds"},
    "pts_asts":       {"points", "assists"},
    "rebs_asts":      {"rebounds", "assists"},
    "blocks_steals":  {"blocks", "steals"},
    "hits_runs_rbis": {"hits", "runs", "rbis"},
}
RHO_COMBO_OVERLAP = 0.60         # base stat + the combo that contains it

MIN_EDGE_FOR_LEG = 0.06
MAX_CANDIDATES   = 16


def _combo_overlap(a: str, b: str) -> bool:
    return (a in _COMBO_COMPONENTS and b in _COMBO_COMPONENTS[a]) or \
           (b in _COMBO_COMPONENTS and a in _COMBO_COMPONENTS[b])


def _pair_rho(leg1: dict, leg2: dict) -> float:
    """Empirical correlation between the two legs' hit outcomes (unsigned)."""
    if leg1["player_id"] == leg2["player_id"]:
        a, b = leg1["stat_type"], leg2["stat_type"]
        if a == b:
            return 0.0
        return (NATURAL_PAIR_RHO.get(frozenset([a, b]))
                or (RHO_COMBO_OVERLAP if _combo_overlap(a, b) else RHO_SAME_PLAYER_DEFAULT))
    if leg1["game_id"] != leg2["game_id"]:
        return 0.0
    t1, t2 = leg1.get("team_id"), leg2.get("team_id")
    if t1 and t2 and t1 == t2:
        return RHO_SAME_TEAM
    return RHO_DIFF_TEAM


def _corr_adj(leg1: dict, leg2: dict) -> float:
    """Covariance term to add to P(A)*P(B): empirical rho on the probability scale,
    signed by direction agreement (opposite over/under flips it negative)."""
    rho = _pair_rho(leg1, leg2)
    if rho == 0.0:
        return 0.0
    if leg1["direction"] != leg2["direction"]:
        rho = -rho
    pa, pb = leg1["model_prob"], leg2["model_prob"]
    return rho * (pa * (1 - pa) * pb * (1 - pb)) ** 0.5


def _joint_prob_2(a: dict, b: dict) -> float:
    p = a["model_prob"] * b["model_prob"] + _corr_adj(a, b)
    return float(np.clip(p, 0.01, 0.99))


def _joint_prob_3(legs: tuple) -> float:
    a, b, c = legs
    base = a["model_prob"] * b["model_prob"] * c["model_prob"]
    # Apply each pairwise covariance scaled by the third leg's probability
    base += _corr_adj(a, b) * c["model_prob"]
    base += _corr_adj(a, c) * b["model_prob"]
    base += _corr_adj(b, c) * a["model_prob"]
    return float(np.clip(base, 0.01, 0.99))


def _joint_prob_4(legs: tuple) -> float:
    a, b, c, d = legs
    base = a["model_prob"] * b["model_prob"] * c["model_prob"] * d["model_prob"]
    pairs = list(itertools.combinations(legs, 2))
    others = [tuple(l for l in legs if l not in pair) for pair in pairs]
    for (x, y), rest in zip(pairs, others):
        prod_rest = float(np.prod([r["model_prob"] for r in rest]))
        base += _corr_adj(x, y) * prod_rest
    return float(np.clip(base, 0.01, 0.99))


def _pairwise_rho(a: dict, b: dict) -> float:
    """Latent Gaussian correlation implied by the prob-scale covariance `_corr_adj`.
    Cov(A,B) = rho * sqrt(pA(1-pA) pB(1-pB)), so rho = corr_adj / that sqrt."""
    adj = _corr_adj(a, b)
    if adj == 0.0:
        return 0.0
    pa, pb = a["model_prob"], b["model_prob"]
    denom = np.sqrt(pa * (1 - pa) * pb * (1 - pb))
    return float(np.clip(adj / denom, -0.95, 0.95)) if denom > 0 else 0.0


def mc_joint_prob(legs: tuple, n_sims: int = 40000) -> float:
    """P(all legs hit) via a Gaussian copula over the legs' pairwise correlations.

    Correct for any number of correlated legs, unlike the first-order analytic sum
    (`_joint_prob_3/4`) which OVERSTATES stacked-parlay EV — e.g. a 4-leg same-team
    stack reads joint .30 / +200% EV analytically vs .24 / +144% here. Seeded →
    deterministic per leg-set. Only invoked when legs are actually correlated."""
    n = len(legs)
    p = np.array([l["model_prob"] for l in legs], dtype=float)
    R = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            R[i, j] = R[j, i] = _pairwise_rho(legs[i], legs[j])
    # nearest-PSD: heuristic pairwise rhos need not be jointly consistent
    w, V = np.linalg.eigh(R)
    R = V @ np.diag(np.clip(w, 1e-6, None)) @ V.T
    d = np.sqrt(np.diag(R)); R = R / np.outer(d, d)
    L = np.linalg.cholesky(R)
    Z = np.random.default_rng(42).standard_normal((n_sims, n)) @ L.T
    hit = (Z < norm.ppf(p)).all(axis=1)
    return float(np.clip(hit.mean(), 0.01, 0.99))


def _joint_prob(legs: tuple) -> float:
    """Exact independent product when no legs are correlated (the common case);
    Gaussian-copula MC when they are (the analytic first-order sum overstated EV)."""
    legs = tuple(legs)
    has_corr = any(_corr_adj(legs[i], legs[j]) != 0.0
                   for i in range(len(legs)) for j in range(i + 1, len(legs)))
    if not has_corr:
        return float(np.clip(np.prod([l["model_prob"] for l in legs]), 0.01, 0.99))
    return mc_joint_prob(legs)


def build_correlated_parlays(picks: pd.DataFrame, top_n: int = 10) -> list[dict]:
    """
    picks must have: player_id, player_name, game_id, team_id, stat_type,
                     line_value, direction, model_prob, edge
    Returns list of combo dicts sorted by EV descending.
    """
    if picks.empty:
        return []

    if "team_id" not in picks.columns:
        picks = picks.copy()
        picks["team_id"] = None

    picks = (
        picks
        .sort_values("edge", ascending=False)
        .drop_duplicates(subset=["player_id", "stat_type", "direction"])
        .reset_index(drop=True)
    )

    strong = picks[picks["edge"] >= MIN_EDGE_FOR_LEG]
    if len(strong) < 2:
        strong = picks.nlargest(min(MAX_CANDIDATES, len(picks)), "edge")
    candidates = strong.nlargest(MAX_CANDIDATES, "edge").to_dict("records")

    combos = []
    for size, mult in MULTIPLIERS.items():
        if len(candidates) < size:
            continue
        for legs in itertools.combinations(candidates, size):
            p_joint = _joint_prob(legs)
            ev      = mult * p_joint - 1
            combos.append({
                "size":      size,
                "multiplier": mult,
                "legs":      legs,
                "p_joint":   round(p_joint, 4),
                "ev":        round(ev, 4),
                "avg_edge":  round(float(np.mean([l["edge"] for l in legs])), 4),
            })

    combos.sort(key=lambda x: x["ev"], reverse=True)
    return combos[:top_n]


def build_slate(picks: pd.DataFrame, sizes: list[int] = None) -> dict[int, dict]:
    """Select the best non-redundant combo per parlay size.

    Generates all combos (same as build_correlated_parlays), then for each
    size picks the highest-EV combo. If the same leg would appear in more
    than one selected combo, a small redundancy penalty is applied to favour
    diversification — but only enough to break ties, not override big EV gaps.

    Returns {size: combo_dict} for each requested size.
    """
    if sizes is None:
        sizes = [2, 3, 4]

    if picks.empty:
        return {}

    if "team_id" not in picks.columns:
        picks = picks.copy()
        picks["team_id"] = None

    picks = (
        picks
        .sort_values("edge", ascending=False)
        .drop_duplicates(subset=["player_id", "stat_type", "direction"])
        .reset_index(drop=True)
    )

    strong = picks[picks["edge"] >= MIN_EDGE_FOR_LEG]
    if len(strong) < 2:
        strong = picks.nlargest(min(MAX_CANDIDATES, len(picks)), "edge")
    candidates = strong.nlargest(MAX_CANDIDATES, "edge").to_dict("records")

    # Build all combos per size
    combos_by_size: dict[int, list] = {s: [] for s in sizes}
    for size in sizes:
        mult = MULTIPLIERS.get(size)
        if mult is None or len(candidates) < size:
            continue
        for legs in itertools.combinations(candidates, size):
            p_joint = _joint_prob(legs)
            ev      = mult * p_joint - 1
            combos_by_size[size].append({
                "size":       size,
                "multiplier": mult,
                "legs":       legs,
                "p_joint":    round(p_joint, 4),
                "ev":         round(ev, 4),
                "avg_edge":   round(float(np.mean([l["edge"] for l in legs])), 4),
            })

    # Select greedily: largest size first, apply small penalty for reused legs
    selected: dict[int, dict] = {}
    used_leg_keys: set[tuple] = set()

    for size in sorted(sizes, reverse=True):
        combos = combos_by_size.get(size, [])
        if not combos:
            continue
        combos.sort(key=lambda x: x["ev"], reverse=True)
        # Score = ev - 0.04 per already-used leg (nudge toward diversity, don't force it)
        def _score(c):
            leg_keys = {(l["player_id"], l["stat_type"], l["direction"]) for l in c["legs"]}
            overlap  = len(leg_keys & used_leg_keys)
            return c["ev"] - 0.04 * overlap

        best = max(combos, key=_score)
        selected[size] = best
        for leg in best["legs"]:
            used_leg_keys.add((leg["player_id"], leg["stat_type"], leg["direction"]))

    return selected


def print_slate(slate: dict[int, dict], title: str = "TODAY'S PICKS", stake: float = 10.0):
    """Print the recommended slate as a clean, actionable picks card."""
    if not slate:
        print("\nNo slate generated.")
        return

    sep = "═" * 58
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)

    for size in sorted(slate.keys(), reverse=True):
        c    = slate[size]
        mult = c["multiplier"]
        win  = round(stake * mult, 2)
        print(f"\n  PLAY — {size}-pick ({mult}x)  │  ${stake:.0f} → ${win:.0f}  │  "
              f"joint={c['p_joint']:.1%}  EV={c['ev']:+.0%}")
        print(f"  {'─'*54}")
        for leg in c["legs"]:
            inj_str  = "  ⚠" if leg.get("injury_flag") else ""
            prob_str = f"({leg['model_prob']:.0%})"
            ctx_str  = f"  O/U {leg['game_total']}" if leg.get("game_total") else ""
            print(
                f"    {leg['player_name']:26s}  "
                f"{leg['stat_type']:14s}  "
                f"{leg['direction'].upper():5s}  "
                f"{leg['line_value']:<6}  "
                f"{prob_str}{ctx_str}{inj_str}"
            )
    print(f"\n{sep}\n")


def print_parlay_recommendations(combos: list[dict]):
    if not combos:
        print("\nNo parlay recommendations generated.")
        return

    print("\n=== Correlated Parlay Recommendations ===")
    for i, c in enumerate(combos, 1):
        print(
            f"\n#{i}  {c['size']}-pick ({c['multiplier']}x)  "
            f"joint={c['p_joint']:.1%}  EV={c['ev']:+.1%}  "
            f"avg_edge={c['avg_edge']:.1%}"
        )
        for leg in c["legs"]:
            injury = "  ⚠ TEAMMATE OUT" if leg.get("injury_flag") else ""
            game_ctx = ""
            if leg.get("game_total"):
                game_ctx = f"  [O/U {leg['game_total']}]"
            implied = ""
            if leg.get("implied_team_total"):
                implied = f"  implied={leg['implied_team_total']}"
            print(
                f"   {leg['player_name']:28s}  {leg['stat_type']:14s}  "
                f"{leg['direction'].upper():5s}  {leg['line_value']}  "
                f"({leg['model_prob']:.0%}){game_ctx}{implied}{injury}"
            )


# ── Diversified (correlation-AVOIDING) parlay ────────────────────────────────
# The functions above EXPLOIT positive correlation (same-team overs underpriced
# by PP's fixed multipliers). That only helps when the model is well-calibrated.
# In practice the slate is near-breakeven, and the observed failure mode is the
# DOWNSIDE of correlation: stacking legs from one game in one direction (e.g. a
# pile of rebounds-unders) means a single game environment — a hot/cold night —
# decides them all together, so they bust as a block. For the user-facing parlay
# we instead DIVERSIFY: spread legs across independent game outcomes.

def build_diversified_parlay(picks_df, max_legs: int = 4):
    """Pick up to ``max_legs`` uncorrelated legs, highest-confidence first.

    Rule: never take two legs from the same (game, direction) — that's the
    strongly-correlated case that sinks a parlay when one game runs hot/cold.
    Different games (or same game opposite directions) are ~independent and
    allowed. Dedups players. Returns the chosen legs ordered by model_prob desc.
    """
    if picks_df is None or len(picks_df) == 0:
        return picks_df
    df = picks_df.copy()
    if "model_prob" in df.columns:
        df = df.sort_values("model_prob", ascending=False)
    df = df.drop_duplicates(subset=["player_id"], keep="first")
    chosen, used = [], set()
    for idx, r in df.iterrows():
        key = (r.get("game_id"), r.get("direction"))
        if key in used:                 # same game + same direction = correlated
            continue
        chosen.append(idx)
        used.add(key)
        if len(chosen) >= max_legs:
            break
    return df.loc[chosen]
