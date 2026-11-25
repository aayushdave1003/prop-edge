"""Unit tests for the Kalshi reference-feed parser (pure functions, no network)."""
from datetime import date

from props.ingest.kalshi_lines import (
    SERIES, parse_market, threshold_to_sleeper_line,
)

GOOD = {
    "ticker": "KXNBAPTS-26NOV24DALDEN-DALPWASHINGTON25-25",
    "yes_sub_title": "P.J. Washington: 25+",
    "yes_bid_dollars": "0.0300",
    "yes_ask_dollars": "0.1600",
    "volume": 12,
}


def test_parse_market_good():
    row, reason = parse_market(GOOD, "KXNBAPTS")
    assert reason is None
    assert row["sport_code"] == "nba" and row["stat_type"] == "points"
    assert row["event_date"] == date(2026, 11, 24)
    assert row["matchup"] == "DALDEN"
    assert row["player_name"] == "P.J. Washington"   # rsplit keeps dotted names
    assert row["threshold"] == 25.0
    assert row["yes_bid"] == 0.03 and row["yes_ask"] == 0.16
    assert row["volume"] == 12.0


def test_parse_market_skips_unquoted_and_malformed():
    unquoted = {**GOOD, "yes_bid_dollars": "0.0000", "yes_ask_dollars": None}
    assert parse_market(unquoted, "KXNBAPTS") == (None, "unquoted")

    bad_shape = {**GOOD, "ticker": "KXNBAPTS-26NOV24DALDEN-25"}
    assert parse_market(bad_shape, "KXNBAPTS") == (None, "ticker_shape")

    bad_thr = {**GOOD, "ticker": "KXNBAPTS-26NOV24DALDEN-DALPWASHINGTON25-X"}
    assert parse_market(bad_thr, "KXNBAPTS") == (None, "threshold")

    bad_date = {**GOOD, "ticker": "KXNBAPTS-99XXX24DALDEN-DALPWASHINGTON25-25"}
    assert parse_market(bad_date, "KXNBAPTS") == (None, "event_date")

    no_name = {**GOOD, "yes_sub_title": "25+"}
    assert parse_market(no_name, "KXNBAPTS") == (None, "player_name")


def test_threshold_maps_to_sleeper_over_line():
    # Kalshi "225+" (>= 225) is exactly the over of a Sleeper 224.5 line
    assert threshold_to_sleeper_line(225) == 224.5
    assert threshold_to_sleeper_line(8) == 7.5


def test_series_map_targets_our_canonical_names():
    """The reference feed is deliberately NOT bound to WAGER_TO_STAT or the
    model registry (passing_yards belongs here with no model). But stat/sport
    names must be OUR canonical spellings, or kalshi_compare's joins silently
    match nothing."""
    known_sports = {"nfl", "nba", "nhl"}
    known_stats = {"passing_yards", "receiving_yards", "rushing_yards",
                   "receptions", "points", "assists", "rebounds",
                   "pts_rebs_asts", "threes_made", "goals"}
    for sport, stat in SERIES.values():
        assert sport in known_sports, sport
        assert stat in known_stats, stat
