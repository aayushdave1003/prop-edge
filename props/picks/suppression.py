"""Pick suppression rules — one documented place for every filter.

These gate which props get logged at all (vs. the per-category *cutoffs* in
`category_cutoffs.py`, which gate which logged picks are *recommended*). They were
previously scattered through `log_picks` as inline constants and magic numbers;
centralizing them makes the policy auditable and tunable in one file.

Each rule and its rationale:

  MIN_EDGE_TO_LOG     model must beat a coin-flip by ≥5pp (model_prob>0.55) to be
                      worth logging. The 57.7% 2-pick breakeven is enforced later
                      by the recommended-tier cutoffs.
  MAX_CONFIDENCE      no genuine single-game prop is >97% certain — a model prob
                      that high means the line is a multi-game/fantasy cumulative
                      the model is pricing as a single game. Drop it.
  MIN_LINE_BY_STAT    trivially low lines (e.g. OVER 2.5 pts) are set for
                      injured/bench returnees and carry no signal.
  MAX_LINE_BY_STAT    absurdly high lines are multi-game accumulations PrizePicks
                      serves alongside standard lines (a WNBA 37-pt line, etc.).
  MIN_MINUTES_HARD    skip players averaging <12 min — too much DNP risk.
  MIN_MINUTES_HIGHVAR NBA bench players averaging <18 min are high-variance role
                      players; suppressed to cut DNP/blowout-minutes noise.
  is_out_status()     suppress players whose injury status means they won't play.
  is_stale_game()     never log a pick for an already-played/past game.
"""

MIN_EDGE_TO_LOG = 0.05        # model_prob > 0.55
MAX_CONFIDENCE = 0.97         # above this = a mis-priced multi-game/fantasy line
MIN_MINUTES_HARD = 12.0       # hard floor: skip players PROJECTED below this
MIN_MINUTES_HIGHVAR = 18.0    # NBA bench role players below this are high-variance
DNP_MINUTES = 8.0             # recent minutes under this = DNP / garbage-time risk

# Minimum line value per stat — filters trivially low lines (OVER 2.5 pts etc.).
MIN_LINE_BY_STAT = {
    # NBA / WNBA
    "points":             5.0,
    "rebounds":           3.0,
    "assists":            1.5,
    "threes_made":        0.5,
    "pts_rebs_asts":     15.0,
    "pts_rebs":          10.0,
    "pts_asts":          10.0,
    "rebs_asts":          5.0,
    "blocks":             0.5,
    "steals":             0.5,
    "blocks_steals":      1.0,
    # MLB
    "strikeouts_pitcher": 2.5,
    "hits":               1.5,
    "rbis":               0.5,
    "total_bases":        1.5,
    "home_runs":          0.5,
    # NHL
    "goals":              0.5,
    "saves":             15.0,
}

# Maximum line value per stat — filters multi-game cumulative / fantasy lines.
MAX_LINE_BY_STAT = {
    # Basketball (NBA/WNBA/NHL share keys — use the NBA max as ceiling)
    "points":             55.0,
    "rebounds":           22.0,
    "assists":            20.0,
    "threes_made":        10.0,
    "pts_rebs_asts":      80.0,
    "pts_rebs":           60.0,
    "pts_asts":           60.0,
    "rebs_asts":          35.0,
    "blocks":              8.0,
    "steals":              8.0,
    "blocks_steals":      12.0,
    # MLB
    "strikeouts_pitcher": 17.0,
    "home_runs":           4.0,
    "hits":                6.0,
    "rbis":               10.0,
    "total_bases":        14.0,
    # NHL
    "goals":               5.0,
    "saves":              50.0,
}

# Injury-status keywords.
PLAYS_ANYWAY = ("day-to-day", "day to day", "questionable", "probable",
                "gtd", "game-time", "available")
WONT_PLAY = ("out", "doubtful", "il", "suspens", "developmental",
             "bereavement", "paternity")


def is_out_status(status: str) -> bool:
    """True if an injury status means the player likely WON'T play tonight.
    Day-to-day / questionable / probable usually play, so we keep those."""
    s = (status or "").lower()
    if any(k in s for k in PLAYS_ANYWAY):
        return False
    return any(k in s for k in WONT_PLAY)


def is_stale_game(game_state: dict, game_id, target_date) -> bool:
    """True if we should NOT log or surface a pick for this game: it's already
    played (final/live), it isn't on the target date (a past game is stale; a
    future game is only a soft early line), or it's an unknown game we can't
    validate.

    Fail-safe: an id missing from game_state (dangling / unresolved placeholder)
    returns True, so a pick is never logged for a game we can't confirm is a
    still-upcoming game on the target date. The old default (return False) let
    post-game lookahead picks and future soft-line picks through when a run fired
    off its morning schedule.
    """
    gstate = game_state.get(int(game_id))
    if gstate is None:
        return True
    status, gdate = gstate
    if status in ("final", "live"):
        return True
    return gdate is None or gdate != target_date


def line_in_range(stat_type: str, line_value: float) -> bool:
    """True if the line is within the plausible single-game band for its stat."""
    lo = MIN_LINE_BY_STAT.get(stat_type, 0.0)
    hi = MAX_LINE_BY_STAT.get(stat_type, float("inf"))
    return lo <= float(line_value) <= hi
