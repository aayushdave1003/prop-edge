"""Time-decay training weights so models track current form over stale seasons.

A game from two seasons ago shouldn't count as much as last week's. We weight each
training row by an exponential decay on its age (half-life in days), passed to
LightGBM as `weight=`. Recent games keep ~full weight; a game one half-life old
counts half. Cheap, no new data, and the A/B gate decides whether it actually
beats the unweighted model.
"""
import pandas as pd

RECENCY_HALFLIFE_DAYS = 365     # a game ~1 year old counts half as much


def recency_weights(dates, halflife: float = RECENCY_HALFLIFE_DAYS):
    """Exponential time-decay weights for a series of game dates (newest = 1.0)."""
    d = pd.to_datetime(pd.Series(list(dates)))
    age = (d.max() - d).dt.days.clip(lower=0)
    return (0.5 ** (age / halflife)).to_numpy()
