"""Unit tests for the market-arbitrage finder (compute_arb) — pure logic, no DB."""
from collections import namedtuple

from props.picks.sleeper_arb import compute_arb, MIN_EDGE, MAX_EDGE

Row = namedtuple("Row", "player_id player_name game_id sport_code stat_type line op up")


def _line(stat="hits", line=0.5, op=1.8, up=1.8):
    return Row(1, "Test Player", 100, "mlb", stat, line, op, up)


def test_flags_soft_line():
    # sharp says 70% over; Sleeper overpays at 1.8x -> EV = .7*1.8-1 = +0.26
    out = compute_arb([_line(op=1.8, up=1.8)], {("test player", "hits"): [(0.5, 0.70)]})
    assert len(out) == 1
    assert out[0]["side"] == "over"
    assert MIN_EDGE < out[0]["ev"] < MAX_EDGE
    assert 0.24 < out[0]["ev"] < 0.28


def test_skips_fairly_priced_line():
    # sharp 70% over, but low payout (1.35x) -> over EV = -.055, under EV = -.60: neither +EV
    out = compute_arb([_line(op=1.35, up=1.35)], {("test player", "hits"): [(0.5, 0.70)]})
    assert out == []


def test_guards_phantom_anchor():
    # anchor no-vig prob 0.95 is outside [0.20,0.80] (alt/thin line) -> skipped
    out = compute_arb([_line(op=1.8, up=1.8)], {("test player", "hits"): [(0.5, 0.95)]})
    assert out == []


def test_caps_implausible_edge():
    # a "70% over at 3.0x" would be +110% EV — a phantom; MAX_EDGE cap rejects it
    out = compute_arb([_line(op=3.0, up=1.2)], {("test player", "hits"): [(0.5, 0.70)]})
    assert out == []  # over EV 1.1 > MAX_EDGE, under EV negative -> nothing survives


def test_no_sharp_reference_skipped():
    out = compute_arb([_line()], {})  # no sharp for this player/stat
    assert out == []


def test_picks_under_when_under_is_the_value():
    # sharp 30% over (=70% under); Sleeper overpays the under at 1.8x -> +EV under
    out = compute_arb([_line(op=1.8, up=1.8)], {("test player", "hits"): [(0.5, 0.30)]})
    assert len(out) == 1
    assert out[0]["side"] == "under"
    assert out[0]["ev"] > MIN_EDGE
