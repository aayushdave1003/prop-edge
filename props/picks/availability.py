"""Player availability / projected-minutes model (basketball).

DNPs and minutes swings are the biggest source of variance in basketball props —
a player who looks fine on a season average can be quietly falling out of the
rotation, or just returning to a big role. The old suppression used a single
flat average, which misses both.

This projects tonight's minutes from the precomputed rolling features
(`last_5/10/20_avg_minutes`, `season_avg_minutes`, `min_stddev_last_10`) with a
recency weighting, and flags **DNP risk** when a player's recent floor is a
non-rotation level or has sharply collapsed. log_picks uses the projection (not a
flat average) for the minutes floor, and drops DNP-risk picks outright.

Pure function over the player's latest `derived` JSON — no new ingest, no model
file; it combines features the daily pipeline already computes.
"""
from props.picks.suppression import MIN_MINUTES_HARD, DNP_MINUTES

# Recency weights for the projected-minutes blend (last-5 matters most).
_WINDOW_WEIGHTS = [("last_5_avg_minutes", 0.5),
                   ("last_10_avg_minutes", 0.3),
                   ("last_20_avg_minutes", 0.2)]

# Teammate-out bump: the team must be losing a real rotation player's minutes,
# and the bumped player must already be enough of a rotation piece to plausibly
# absorb them — a deep-bench scrub doesn't suddenly play because a starter sits.
TEAMMATE_OUT_MIN = 15.0       # team minutes lost to injury before we bump anyone
ROTATION_FLOOR = 5.0          # the player's own recent floor must clear this


def teammate_bump_from_injury(derived: dict, team_minutes_out: float) -> float:
    """Convert tonight's team injury context (sum of out rotation-teammate minutes,
    i.e. detect_injury_expansion's value) into a minutes bump for THIS player.

    Returns 0 unless a meaningful injury exists AND the player is already a
    plausible rotation piece — then we expect them to clear the minutes floor, so
    we don't suppress them. Conservative: it rescues fringe rotation players on a
    depleted team without inflating a scrub who still won't play."""
    if (team_minutes_out or 0.0) < TEAMMATE_OUT_MIN:
        return 0.0
    floor = _f(derived, "last_5_avg_minutes")
    if floor is None:
        floor = _f(derived, "season_avg_minutes") or 0.0
    if floor < ROTATION_FLOOR:
        return 0.0
    return MIN_MINUTES_HARD + 2.0


def _f(derived: dict, key: str):
    v = derived.get(key) if derived else None
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def project_minutes(derived: dict, teammate_bump: float = 0.0) -> dict:
    """Project tonight's minutes + a DNP-risk flag from the rolling features.

    teammate_bump: the player's expected minutes when a key teammate is out
    (e.g. `absent_teammate_avg_min`), applied when that's the case tonight — a
    bench player's minutes rise when a starter sits, so we don't suppress them."""
    windows = [(_f(derived, k), w) for k, w in _WINDOW_WEIGHTS]
    avail = [(v, w) for v, w in windows if v is not None]
    if avail:
        tw = sum(w for _, w in avail)
        base = sum(v * w for v, w in avail) / tw
    else:
        base = _f(derived, "season_avg_minutes") or 0.0

    projected = max(base, float(teammate_bump or 0.0))

    l5 = _f(derived, "last_5_avg_minutes")
    l20 = _f(derived, "last_20_avg_minutes")
    ref = l5 if l5 is not None else base

    # DNP risk: a recent non-rotation floor, or a sharp minutes collapse that the
    # longer windows haven't caught up to yet (falling out of the rotation).
    dnp_risk = False
    reason = ""
    if ref < DNP_MINUTES and (teammate_bump or 0.0) < MIN_MINUTES_HARD:
        dnp_risk, reason = True, f"recent floor {ref:.0f}m < {DNP_MINUTES:.0f}m"
    elif l5 is not None and l20 and l5 < 0.55 * l20 and l5 < 18:
        dnp_risk, reason = True, f"minutes collapsing ({l5:.0f}m vs {l20:.0f}m)"

    return {"projected": round(projected, 1), "dnp_risk": dnp_risk, "reason": reason}


def should_suppress(derived: dict, teammate_bump: float = 0.0) -> tuple[bool, str]:
    """True (+reason) if a basketball pick should be dropped on availability —
    projected minutes below the hard floor, or a DNP-risk pattern."""
    proj = project_minutes(derived, teammate_bump)
    if proj["projected"] < MIN_MINUTES_HARD:
        return True, f"projected {proj['projected']:.0f}m < {MIN_MINUTES_HARD:.0f}m floor"
    if proj["dnp_risk"]:
        return True, proj["reason"]
    return False, ""
