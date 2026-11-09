"""Pairing + EV logic for the QB+own-WR stack lane (props.picks.nfl_stacks).

The pairing is the part a silent bug would poison: a QB stacked with an
OPPONENT's receiver (or a receiver in a different game) is not mechanically
correlated, so logging it would dilute the lane's forward record with noise
and the ROI verdict would be about the wrong bet.
"""
from collections import namedtuple

from props.picks.nfl_stacks import build_stacks

Leg = namedtuple("Leg", "player_id player_name team_id game_id stat_type line over_payout")

QB = Leg(1, "QB A", 10, 100, "passing_yards", 225.5, 1.8)
WR_SAME = Leg(2, "WR B", 10, 100, "receiving_yards", 55.5, 1.75)
WR_OPP = Leg(3, "WR C", 11, 100, "receiving_yards", 40.5, 1.7)
WR_OTHER_GAME = Leg(4, "WR D", 10, 101, "receiving_yards", 40.5, 1.7)


def test_pairs_only_same_team_same_game():
    stacks = build_stacks([QB, WR_SAME, WR_OPP, WR_OTHER_GAME], joint_est=0.381)
    assert [(s["qb_player_id"], s["wr_player_id"]) for s in stacks] == [(1, 2)]


def test_payout_is_product_of_leg_multipliers():
    s = build_stacks([QB, WR_SAME], joint_est=0.381)[0]
    assert s["payout"] == round(1.8 * 1.75, 4)
    assert s["ev_est"] == round(0.381 * 1.8 * 1.75 - 1.0, 4)


def test_thin_probe_sample_means_no_ev_estimate():
    s = build_stacks([QB, WR_SAME], joint_est=None)[0]
    assert s["joint_est"] is None and s["ev_est"] is None   # logged, not hyped


def test_qb_without_a_team_is_skipped_not_guessed():
    qb_nt = QB._replace(team_id=None)
    assert build_stacks([qb_nt, WR_SAME], joint_est=0.381) == []


def test_each_qb_pairs_only_his_own_receivers():
    qb2 = Leg(5, "QB E", 11, 100, "passing_yards", 240.5, 1.9)
    wr2 = Leg(6, "WR F", 11, 100, "receiving_yards", 60.5, 1.8)
    stacks = build_stacks([QB, qb2, WR_SAME, wr2], joint_est=0.381)
    assert {(s["qb_player_id"], s["wr_player_id"]) for s in stacks} == {(1, 2), (5, 6)}


def test_self_pair_is_impossible():
    qb_with_rec_line = Leg(1, "QB A", 10, 100, "receiving_yards", 5.5, 1.7)
    stacks = build_stacks([QB, qb_with_rec_line], joint_est=0.381)
    assert stacks == []
