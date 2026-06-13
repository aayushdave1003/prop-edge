"""Per-sport market-disagreement filter.

Sharp prop markets price some sports far more tightly than others, so a large
model-vs-market gap means different things by sport. On settled picks:

    NBA   model edge >15% over the no-vig market won 45%  (vs 55% at 5-15%)
    MLB   model edge >15%                       won 62%  (vs 48% at 5-15%)

NBA prop markets are deep and efficient: when the model *wildly* disagrees with
the market there, it's usually the model erroring, so we **drop** those picks.
MLB prop markets are softer, so the same disagreement is genuine edge — left
alone. Only sports whose markets are sharp enough to trust over the model are
filtered; everything else passes through.

The signal is ``market_edge`` (model_prob − market-implied prob for the picked
side), populated only when The Odds API line exists — so this is a **no-op until
the odds feed is live** (market_edge is None/NaN), and never touches sports we
don't have a sharp market for.
"""

# Sports whose prop markets are efficient enough to trust over a wild model gap.
SHARP_MARKET_SPORTS = {"nba"}
# How far the model may exceed the market before we treat the pick as a likely
# model error (model_prob − market_implied). 0.15 = the bucket that underperforms.
DISAGREE_MAX = 0.15


def market_disagrees(sport, market_edge) -> bool:
    """True if this pick should be dropped for disagreeing too hard with a sharp
    market. False when there's no market line (feed off) or the sport's market
    isn't sharp enough to trust."""
    if sport not in SHARP_MARKET_SPORTS or market_edge is None:
        return False
    try:
        me = float(market_edge)
    except (TypeError, ValueError):
        return False
    if me != me:                      # NaN — no usable market line
        return False
    return me > DISAGREE_MAX
