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

MULTIPLIERS = {2: 3.0, 3: 5.0, 4: 10.0}

# Correlation adjustments to joint probability P(A) * P(B) + adj
# Rough calibrated estimates; directionally correct even if not exact.
CORR_SAME_TEAM_BOTH_OVER  =  0.06
CORR_SAME_TEAM_BOTH_UNDER =  0.04
CORR_SAME_TEAM_OPPOSITE   = -0.10   # one over / one under — anti-correlated, avoid
CORR_DIFF_TEAM_BOTH_OVER  =  0.03   # both benefit from high-scoring game
CORR_NATURAL_PAIR         =  0.08   # naturally linked stats for same player

NATURAL_STAT_PAIRS = {
    frozenset(["points", "assists"]),
    frozenset(["points", "pts_rebs_asts"]),
    frozenset(["assists", "pts_rebs_asts"]),
    frozenset(["rebounds", "pts_rebs_asts"]),
    frozenset(["points", "pts_asts"]),
    frozenset(["points", "pts_rebs"]),
    frozenset(["rebounds", "pts_rebs"]),
    frozenset(["assists", "rebs_asts"]),
    frozenset(["rebounds", "rebs_asts"]),
}

MIN_EDGE_FOR_LEG = 0.06
MAX_CANDIDATES   = 16


def _corr_adj(leg1: dict, leg2: dict) -> float:
    """Estimate covariance term to add to P(A)*P(B)."""
    same_player = leg1["player_id"] == leg2["player_id"]
    same_game   = leg1["game_id"] == leg2["game_id"]
    t1, t2      = leg1.get("team_id"), leg2.get("team_id")
    same_team   = (not same_player) and same_game and t1 and t2 and (t1 == t2)
    diff_team   = (not same_player) and same_game and t1 and t2 and (t1 != t2)
    both_over   = leg1["direction"] == "over"  and leg2["direction"] == "over"
    both_under  = leg1["direction"] == "under" and leg2["direction"] == "under"
    opposite    = leg1["direction"] != leg2["direction"]

    if same_player:
        pair = frozenset([leg1["stat_type"], leg2["stat_type"]])
        return CORR_NATURAL_PAIR if pair in NATURAL_STAT_PAIRS else 0.0

    if same_team:
        if opposite:     return CORR_SAME_TEAM_OPPOSITE
        if both_over:    return CORR_SAME_TEAM_BOTH_OVER
        if both_under:   return CORR_SAME_TEAM_BOTH_UNDER

    if diff_team and both_over:
        return CORR_DIFF_TEAM_BOTH_OVER

    return 0.0


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


def _joint_prob(legs: tuple) -> float:
    n = len(legs)
    if n == 2: return _joint_prob_2(*legs)
    if n == 3: return _joint_prob_3(legs)
    if n == 4: return _joint_prob_4(legs)
    # Fallback: independent
    return float(np.clip(np.prod([l["model_prob"] for l in legs]), 0.01, 0.99))


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
