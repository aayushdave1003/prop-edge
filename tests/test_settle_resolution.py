"""Every market we model must be resolvable from a real box score.

7,079 `hits_runs_rbis` picks -- the highest-volume MLB market -- sat un-graded
from launch until 2026-10-08 because `_resolve_stat` looked for a box-score key
named "hits_runs_rbis" that has never existed. The failure was silent: the pick
logged `stat_not_in_box`, was counted as `missing`, and stayed unsettled, so it
never reached the win rate, the ROI, or the per-category cutoffs that auto-tune
off settled history.

The box-score fixtures below are the REAL key sets, read from prod on
2026-10-08. Keep them honest: if an ingest starts emitting different keys, this
test should be updated from prod, not loosened.
"""
import pytest

from props.picks.settle_picks import _resolve_stat
from props.models.registry import MODELS

# Real box-score key sets per sport (values are arbitrary, keys are not).
BOX = {
    "mlb": {k: 1 for k in [
        "at_bats", "bat_order_spot", "batters_faced", "doubles", "earned_runs",
        "hits", "hits_allowed", "home_runs", "home_runs_allowed", "left_on_base",
        "outs_recorded", "pitches_thrown", "plate_appearances", "rbis", "runs",
        "runs_allowed", "stolen_bases", "strikeouts", "strikeouts_pitcher",
        "total_bases", "triples", "walks", "walks_allowed"]},
    "nba": {k: 1 for k in [
        "assists", "blocks", "def_rebounds", "fg3_attempted", "fg3_made",
        "fg_attempted", "fg_made", "ft_attempted", "ft_made", "minutes",
        "off_rebounds", "personal_fouls", "plus_minus", "points", "rebounds",
        "steals", "threes_attempted", "threes_made", "turnovers"]},
    "wnba": {k: 1 for k in [
        "assists", "blocks", "def_rebounds", "fg_attempted", "fg_made",
        "ft_attempted", "ft_made", "minutes", "off_rebounds", "personal_fouls",
        "plus_minus", "points", "rebounds", "steals", "threes_attempted",
        "threes_made", "turnovers"]},
    "nfl": {k: 1 for k in [
        "carries", "receiving_tds", "receiving_yards", "receptions",
        "rushing_tds", "rushing_yards", "targets"]},
    # NHL splits by position: skaters carry no "saves", goalies do. A saves
    # market only ever lands on a goalie row, so that is the row to test against.
    "nhl": {k: 1 for k in [
        "assists", "blocked_shots", "goals", "goals_against", "hits", "minutes",
        "points", "save_pct", "saves", "shots", "shots_against"]},
}

MARKETS = sorted({(m.sport_code, m.stat_type) for m in MODELS})


@pytest.mark.parametrize("sport,stat", MARKETS)
def test_every_modelled_market_resolves(sport, stat):
    box = BOX.get(sport)
    assert box is not None, f"no box-score fixture for sport {sport!r}"
    assert _resolve_stat(box, stat) is not None, (
        f"{sport}/{stat} cannot be read from a {sport} box score -- picks on it "
        f"would never settle"
    )


def test_combo_sums_its_parts():
    box = {"hits": 1, "runs": 2, "rbis": 3}
    assert _resolve_stat(box, "hits_runs_rbis") == 6.0


def test_combo_needs_every_part():
    """A partial box score must yield None, not a silently wrong total."""
    assert _resolve_stat({"hits": 1, "runs": 2}, "hits_runs_rbis") is None


def test_alias_picks_the_right_side_of_a_pair():
    """strikeouts_batter and strikeouts_pitcher are different numbers on the
    same row -- the alias must not read the pitcher's line for a batter prop."""
    box = {"strikeouts": 2, "strikeouts_pitcher": 9, "earned_runs": 4}
    assert _resolve_stat(box, "strikeouts_batter") == 2
    assert _resolve_stat(box, "strikeouts_pitcher") == 9
    assert _resolve_stat(box, "earned_runs_allowed") == 4


def test_unknown_stat_is_none():
    assert _resolve_stat({"hits": 1}, "not_a_real_stat") is None
