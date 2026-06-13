"""Unit tests for prop-edge's pure logic (E14).

These cover the bug-prone, DB-independent functions: settle classification,
the derived-writer coercion + prod-backfill guard, moneyline de-vig, and the
Streamlit HTML sanitizer that the card-rendering bug hinged on.
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── settle classification ─────────────────────────────────────────────────────
from props.picks.settle_picks import classify


@pytest.mark.parametrize("actual,line,direction,expected", [
    (30, 25.5, "over", "win"),
    (20, 25.5, "over", "loss"),
    (20, 25.5, "under", "win"),
    (30, 25.5, "under", "loss"),
    (25, 25, "over", "push"),     # exact line = push regardless of direction
    (25, 25, "under", "push"),
])
def test_classify(actual, line, direction, expected):
    assert classify(actual, line, direction) == expected


# ── derived_writer coercion + prod guard ──────────────────────────────────────
from props.features.derived_writer import feat_dict, _guard_prod_backfill


def test_feat_dict_coerces_nan_and_types():
    # dict preserves per-value dtypes (a mixed pd.Series would upcast ints to float)
    row = {"a": np.float64(1.5), "b": np.int64(3), "c": np.nan}
    out = feat_dict(row, ["a", "b", "c"])
    assert out == {"a": 1.5, "b": 3, "c": 0}
    assert isinstance(out["b"], int) and isinstance(out["a"], float)


def test_guard_allows_local(monkeypatch):
    monkeypatch.setenv("DERIVED_BACKFILL_ALL", "1")
    monkeypatch.delenv("DERIVED_ALLOW_PROD_BACKFILL", raising=False)
    # local engine host -> allowed (no raise)
    _guard_prod_backfill()


def test_guard_blocks_remote(monkeypatch):
    monkeypatch.setenv("DERIVED_BACKFILL_ALL", "1")
    monkeypatch.delenv("DERIVED_ALLOW_PROD_BACKFILL", raising=False)
    import props.features.derived_writer as dw
    fake_engine = types.SimpleNamespace(url=types.SimpleNamespace(host="interchange.proxy.rlwy.net"))
    monkeypatch.setattr(dw, "engine", fake_engine)
    with pytest.raises(RuntimeError):
        dw._guard_prod_backfill()


def test_guard_noop_without_flag(monkeypatch):
    monkeypatch.delenv("DERIVED_BACKFILL_ALL", raising=False)
    import props.features.derived_writer as dw
    fake_engine = types.SimpleNamespace(url=types.SimpleNamespace(host="remote.example.com"))
    monkeypatch.setattr(dw, "engine", fake_engine)
    _guard_prod_backfill()  # no flag -> no raise even on remote


# ── dashboard helpers (loaded without running the page or touching a DB) ──────
def _dashboard_ns():
    sys.modules["streamlit"] = MagicMock()
    fake = types.ModuleType("props.maintenance.migrate")
    fake.run_migrations = lambda: 0
    sys.modules["props.maintenance.migrate"] = fake
    src = (ROOT / "ui" / "dashboard.py").read_text()
    ns = {"__file__": str(ROOT / "ui" / "dashboard.py"), "__name__": "dash_test"}
    exec(compile(src[: src.index("# ── Header")], "dashboard_defs", "exec"), ns)
    return ns


DASH = _dashboard_ns()


def test_american_to_prob():
    f = DASH["_american_to_prob"]
    assert abs(f(-162) - 0.6183) < 0.001     # favorite
    assert abs(f(+136) - 0.4237) < 0.001     # underdog
    assert f(None) is None
    assert f("OFF") is None


def test_html_sanitizer_no_codeblock_trigger():
    # blank line + indented tag is what made Streamlit render raw HTML as code
    _html = DASH["_html"]
    messy = "\n<div>\n\n    <span>x</span>\n  <b>y</b>\n"
    out = _html(messy)
    lines = out.split("\n")
    assert all(line.strip() for line in lines)          # no blank lines
    assert not any(line.startswith("    ") for line in lines)  # no leading indent
    assert "<span>x</span>" in out and "<b>y</b>" in out


def test_form_dots_direction():
    form_dots_html = DASH["form_dots_html"]
    # over pick: a True (went over) is a hit; under pick: True is a miss
    over = form_dots_html([True, False, None], "over")
    under = form_dots_html([True, False, None], "under")
    assert over.count("dot hit") == 1 and over.count("dot miss") == 1
    assert under.count("dot hit") == 1 and under.count("dot miss") == 1
    assert over.count("dot empty") == 1  # None renders empty


# ── injury suppression ────────────────────────────────────────────────────────
from props.picks.log_picks import _is_out_status


def test_is_out_status():
    for s in ["Out", "Doubtful", "15-Day-IL", "60-Day-IL", "7-Day IL",
              "suspension", "developmental list", "paternity"]:
        assert _is_out_status(s), s
    for s in ["Day-To-Day", "Questionable", "Probable", "GTD", "Available", "", None]:
        assert not _is_out_status(s), s


# ── closing line value ────────────────────────────────────────────────────────
from props.picks.compute_clv import clv_points


def test_clv_points_sign_convention():
    # OVER: line moved UP (we got the easier number) -> positive CLV
    assert clv_points(5.5, 6.5, "over") == pytest.approx(1.0)
    assert clv_points(5.5, 4.5, "over") == pytest.approx(-1.0)
    # UNDER: line moved DOWN (we had more room) -> positive CLV
    assert clv_points(5.5, 4.5, "under") == pytest.approx(1.0)
    assert clv_points(5.5, 6.5, "under") == pytest.approx(-1.0)
    # no movement -> zero; missing data -> None
    assert clv_points(5.5, 5.5, "over") == 0.0
    assert clv_points(None, 5.5, "over") is None
    assert clv_points(5.5, None, "under") is None


# ── diversified (correlation-avoiding) parlay ────────────────────────────────
from props.picks.build_parlays import build_diversified_parlay


def test_diversified_parlay_avoids_same_game_same_direction():
    df = pd.DataFrame([
        dict(player_id=1, game_id=99, direction="under", model_prob=0.90),
        dict(player_id=2, game_id=99, direction="under", model_prob=0.85),  # corr — skip
        dict(player_id=3, game_id=99, direction="under", model_prob=0.80),  # corr — skip
        dict(player_id=4, game_id=99, direction="over",  model_prob=0.78),  # ok (opp dir)
        dict(player_id=5, game_id=42, direction="under", model_prob=0.72),  # ok (diff game)
    ])
    out = build_diversified_parlay(df, max_legs=4)
    # never two legs sharing (game, direction)
    keys = list(zip(out["game_id"], out["direction"]))
    assert len(keys) == len(set(keys))
    assert set(out["player_id"]) == {1, 4, 5}      # the two redundant unders dropped
    assert list(out["player_id"])[0] == 1           # highest confidence first


def test_diversified_parlay_dedups_players():
    df = pd.DataFrame([
        dict(player_id=1, game_id=1, direction="under", model_prob=0.8),
        dict(player_id=1, game_id=1, direction="over",  model_prob=0.7),  # same player
        dict(player_id=2, game_id=2, direction="under", model_prob=0.6),
    ])
    out = build_diversified_parlay(df, max_legs=4)
    assert list(out["player_id"]) == [1, 2]


# ── sport resolution (combo-model mislabel guard) ────────────────────────────
from props.picks.log_picks import sport_for_model


def test_sport_for_model_resolves_combo_and_prefixes():
    # The bug: nba_combo_derived isn't in the registry and was defaulting to mlb.
    assert sport_for_model("nba_combo_derived") == "nba"
    assert sport_for_model("wnba_points_v1") == "wnba"   # wnba before nba
    assert sport_for_model("nhl_goals_v1") == "nhl"
    assert sport_for_model("hits_v1") == "mlb"           # no prefix -> default
    assert sport_for_model("x", {"x": "nhl"}) == "nhl"   # registry wins


# ── feature lookahead-safety (leakage audit guard) ───────────────────────────
from props.features.mlb_rolling import compute_rolling_features, ALL_STATS


def _toy_player_history(last_game_value: float) -> pd.DataFrame:
    """5 chronological games for one player; the LAST game's raw stats are set to
    `last_game_value` so we can prove they don't leak into that game's features."""
    rows = []
    for i in range(5):
        row = {"game_date": pd.Timestamp("2026-04-01") + pd.Timedelta(days=i),
               "player_game_id": 100 + i, "season": "2026"}
        for s in ALL_STATS:
            row[s] = float(i + 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    for s in ALL_STATS:                       # mutate ONLY the last game's stats
        df.loc[df.index[-1], s] = last_game_value
    return df


def test_rolling_features_have_no_lookahead():
    base = compute_rolling_features(_toy_player_history(3.0))
    mutated = compute_rolling_features(_toy_player_history(999.0))
    # The last game's rolling/season features must be identical regardless of the
    # last game's own outcome — they may only use prior games (shift(1) first).
    # If a future change drops a shift(1), these diverge and this test fails.
    feat_cols = [c for c in base.columns
                 if c.startswith(("last_", "season_avg_"))]
    assert len(feat_cols) > 10
    lb, lm = base.iloc[-1], mutated.iloc[-1]
    for c in feat_cols:
        assert lb[c] == lm[c], f"lookahead leak: {c} changed with the current game"


# ── per-category cutoffs (#3) ─────────────────────────────────────────────────
from props.models import category_cutoffs as cc


def test_wilson_lower_bound_penalises_small_n():
    # same win rate, smaller sample => lower bound is lower (less trust)
    assert cc.wilson_lower_bound(7, 10) < cc.wilson_lower_bound(70, 100)
    # all wins still isn't certainty
    assert cc.wilson_lower_bound(10, 10) < 1.0
    assert cc.wilson_lower_bound(0, 0) == 0.0


def _rows(sport, stat, prob, wins, losses):
    return ([{"sport": sport, "stat_type": stat, "model_prob": prob, "win": 1}] * wins
            + [{"sport": sport, "stat_type": stat, "model_prob": prob, "win": 0}] * losses)


def test_compute_picks_lowest_qualifying_cutoff():
    # a clearly +EV book (80% over 60 picks at prob 0.60) qualifies at the floor
    table = cc.compute_cutoffs(_rows("mlb", "hits", 0.60, 48, 12))
    assert table["sports"]["mlb"]["status"] == "tuned"
    assert table["sports"]["mlb"]["cutoff"] == cc.GRID[0]  # lowest grid point


def test_stat_cutoff_steps_up_to_safer_slice():
    # The 0.55 floor clears breakeven, but a higher-confidence slice is markedly
    # safer -> the per-stat cutoff steps up to capture it (the MLB-hits pattern:
    # 70% @0.55 vs 84% @0.625). The coarse SPORT fallback stays at the floor.
    rows = (_rows("mlb", "hits", 0.55, 33, 22)     # ~60% near the floor
            + _rows("mlb", "hits", 0.70, 53, 7))   # ~88% high-confidence slice
    table = cc.compute_cutoffs(rows)
    stat = table["stats"]["mlb|hits"]
    assert stat["cutoff"] > cc.GRID[0]             # stepped up off the 0.55 floor
    assert stat["win_rate"] > 0.80                 # captured the safer slice
    assert table["sports"]["mlb"]["cutoff"] == cc.GRID[0]  # fallback stays permissive


def test_compute_suppresses_losing_model():
    # coin-flip with plenty of data => never clears breakeven => suppressed
    table = cc.compute_cutoffs(_rows("nba", "points", 0.65, 50, 50))
    assert table["sports"]["nba"]["status"] == "suppressed"
    assert table["sports"]["nba"]["cutoff"] == cc.SUPPRESS_CUTOFF


def test_compute_unproven_when_too_little_data():
    table = cc.compute_cutoffs(_rows("wnba", "points", 0.62, 2, 0))
    assert table["sports"]["wnba"]["status"] == "unproven"
    assert table["sports"]["wnba"]["cutoff"] == cc.DEFAULT_CUTOFF


# ── ESPN NBA boxscore parsing (datacenter ingest) ─────────────────────────────
from props.ingest.nba_boxscores import parse_stats


def test_parse_stats_maps_by_key_not_position():
    # Real ESPN NBA column order (REB before AST, OREB/DREB late) — the bug we
    # guard against is reusing WNBA's positional order.
    keys = ['minutes', 'points', 'fieldGoalsMade-fieldGoalsAttempted',
            'threePointFieldGoalsMade-threePointFieldGoalsAttempted',
            'freeThrowsMade-freeThrowsAttempted', 'rebounds', 'assists',
            'turnovers', 'steals', 'blocks', 'offensiveRebounds',
            'defensiveRebounds', 'fouls', 'plusMinus']
    stats = ['31', '17', '5-12', '3-6', '4-4', '3', '0', '0', '1', '1', '0', '3', '0', '-6']
    out = parse_stats(keys, stats)
    assert out["points"] == 17
    assert out["rebounds"] == 3 and out["assists"] == 0
    assert out["fg_made"] == 5 and out["fg_attempted"] == 12
    assert out["threes_made"] == 3 and out["threes_attempted"] == 6
    assert out["off_rebounds"] == 0 and out["def_rebounds"] == 3
    assert out["steals"] == 1 and out["blocks"] == 1
    assert out["plus_minus"] == -6.0 and out["minutes"] == 31.0


def test_parse_stats_handles_dnp_empty():
    # A DNP athlete has an empty stats list -> all zeros, no crash.
    out = parse_stats(["minutes", "points"], [])
    assert out["points"] == 0 and out["minutes"] == 0.0


def test_wilson_upper_bound_above_lower():
    assert cc.wilson_upper_bound(46, 100) > cc.wilson_lower_bound(46, 100)
    assert cc.wilson_upper_bound(0, 0) == 1.0


def test_compute_suppresses_confidently_losing_stat():
    # A stat with plenty of data stuck well below breakeven (like NBA points on
    # the playoff sample) is SUPPRESSED, not left to inherit the sport cutoff.
    rows = (_rows("nba", "points", 0.70, 36, 43)        # ~46% over 79
            + _rows("nba", "rebounds", 0.62, 45, 20))   # sport stays viable
    table = cc.compute_cutoffs(rows)
    assert table["stats"]["nba|points"]["status"] == "suppressed"
    assert table["stats"]["nba|points"]["cutoff"] == cc.SUPPRESS_CUTOFF
    assert cc.rec_cutoff("nba", "points", table=table) == cc.SUPPRESS_CUTOFF


# ── model/market blend weights ────────────────────────────────────────────────
from props.models import blend_weights as bw


def test_blend_no_market_returns_pure_model():
    # The critical guard: no real line -> NEVER blend on a prior, return model.
    w = {"default_w": 1.0, "sports": {"nba": 0.15}}
    assert bw.blend("nba", 0.80, None, w) == 0.80


def test_blend_applies_per_sport_weight():
    w = {"default_w": 1.0, "sports": {"nba": 0.15, "mlb": 0.75}}
    # NBA leans on the market (85%): 0.15*0.9 + 0.85*0.5 = 0.56
    assert bw.blend("nba", 0.90, 0.50, w) == pytest.approx(0.56)
    # MLB leans on the model (75%): 0.75*0.90 + 0.25*0.50 = 0.80
    assert bw.blend("mlb", 0.90, 0.50, w) == pytest.approx(0.80)
    # unknown sport -> default (pure model)
    assert bw.blend("nhl", 0.90, 0.50, w) == pytest.approx(0.90)


def test_fit_weights_recovers_market_when_model_is_noise():
    # model is pure noise (0.5 always), market tracks the outcome -> w -> 0.
    import random
    random.seed(1)
    rows = []
    for _ in range(80):
        y = 1 if random.random() < 0.6 else 0
        rows.append((0.5, 0.6 if y else 0.4, y))   # market separates, model doesn't
    tbl = bw.fit_weights({"nba": rows})
    assert tbl["sports"]["nba"]["status"] == "tuned"
    assert tbl["sports"]["nba"]["w"] <= 0.25        # leans hard on the market


def test_fit_weights_untuned_below_min_n():
    tbl = bw.fit_weights({"mlb": [(0.7, 0.6, 1)] * 5})
    assert tbl["sports"]["mlb"]["status"] == "untuned"
    assert tbl["sports"]["mlb"]["w"] == bw.DEFAULT_W


# ── probability recalibration (Platt) ─────────────────────────────────────────
from props.models import prob_calibration as pcal


def test_calibrate_identity_when_too_little_data():
    # Below MIN_N_CALIB -> identity map (don't calibrate on noise).
    params = pcal.fit_platt([0.8, 0.6], [1, 0])
    assert params["status"] == "identity"
    assert pcal.calibrate(0.84, params) == pytest.approx(0.84)


def test_platt_shrinks_overconfident_model():
    # Build an over-confident set: predicted ~0.85 but only ~60% actually win.
    import random
    random.seed(0)
    probs, wins = [], []
    for _ in range(400):
        probs.append(0.85)
        wins.append(1 if random.random() < 0.60 else 0)
    params = pcal.fit_platt(probs, wins)
    assert params["status"] == "fit"
    assert params["a"] < 1.0                       # slope < 1 => shrinks extremes
    cal = pcal.calibrate(0.85, params)
    assert cal < 0.85                              # pulled down toward the truth
    assert abs(cal - 0.60) < 0.08                  # lands near the real rate


def test_calibrate_is_monotonic_and_bounded():
    params = {"a": 0.65, "b": -0.12, "n": 500, "status": "fit"}
    xs = [0.5, 0.6, 0.7, 0.8, 0.9, 0.99]
    cals = [pcal.calibrate(x, params) for x in xs]
    assert all(b > a for a, b in zip(cals, cals[1:]))   # order preserved
    assert all(0 < c < 1 for c in cals)


# ── daily walk-forward backtest ───────────────────────────────────────────────
from datetime import date
from props.picks import daily_backtest as dbt


def test_roi_2pick_breakeven():
    # A 2-pick power play (3x for 2/2) breaks even at the 57.7% per-leg line.
    assert dbt.roi_2pick(dbt.BREAKEVEN) == pytest.approx(0.0, abs=2e-3)
    assert dbt.roi_2pick(1.0) == pytest.approx(2.0)      # both legs certain -> 3x
    assert dbt.roi_2pick(0.50) < 0                       # coin flip loses


class _R(types.SimpleNamespace):
    """Stand-in for a settled-pick row (attribute access like the SQL Row)."""


def _pick(sport, stat, prob, result, d=None):
    return _R(sport_code=sport, stat_type=stat, model_prob=prob,
              leg_result=result, direction="over",
              d=d or date.today())


def test_calibration_brier_and_perfect_split():
    # Perfectly confident + correct -> Brier 0; perfectly wrong -> Brier 1.
    assert dbt.calibration([_pick("mlb", "hits", 1.0, "win")])["brier"] == pytest.approx(0.0)
    assert dbt.calibration([_pick("mlb", "hits", 1.0, "loss")])["brier"] == pytest.approx(1.0)
    assert dbt.calibration([])["brier"] is None


def test_cutoff_sweep_flags_profitable_suppressed_bucket():
    # A stat suppressed live (cutoff 0.99 -> 0 qualifying) but with a clearly
    # profitable high-confidence slice should be flagged material; a suppressed
    # stat whose best slice still loses should NOT be.
    table = {"default_cutoff": 0.70, "sports": {"nba": {"cutoff": 0.70}},
             "stats": {"nba|points": {"cutoff": 0.99}, "nba|assists": {"cutoff": 0.99}}}
    # points: 20 picks at 0.85 prob, 90% win -> Wilson-LB clears breakeven
    # comfortably, so the suppressed-but-proven slice is flagged material.
    rows = [_pick("nba", "points", 0.85, "win" if i < 18 else "loss") for i in range(20)]
    # assists: 20 picks at 0.85 prob but only 40% win -> losing, stay suppressed
    rows += [_pick("nba", "assists", 0.85, "win" if i < 8 else "loss") for i in range(20)]
    findings = {f["stat"]: f for f in dbt.cutoff_sweep(rows, table)}
    assert findings["points"]["material"] is True
    assert findings["assists"]["material"] is False


def test_cutoff_sweep_ignores_small_sample_hot_streak():
    # A high raw win rate that does NOT clear breakeven on its Wilson lower bound
    # (e.g. 67% on n=18 -> LB ~0.55) is a small-sample mirage, not a real edge:
    # a suppressed stat with only that slice must NOT be flagged material.
    table = {"default_cutoff": 0.70, "sports": {"nba": {"cutoff": 0.70}},
             "stats": {"nba|points": {"cutoff": 0.99}}}
    rows = ([_pick("nba", "points", 0.85, "win" if i < 12 else "loss") for i in range(18)]
            + [_pick("nba", "points", 0.60, "loss") for i in range(40)])  # losing low band
    findings = {f["stat"]: f for f in dbt.cutoff_sweep(rows, table)}
    assert findings["points"]["material"] is False


def test_compute_does_not_suppress_borderline_stat():
    # ~53% with modest n is NOT confidently losing -> defer to sport, no override.
    table = cc.compute_cutoffs(_rows("mlb", "strikeouts_pitcher", 0.66, 38, 34))
    assert "mlb|strikeouts_pitcher" not in table["stats"]


def test_rec_cutoff_hierarchy():
    table = {
        "default_cutoff": 0.70,
        "sports": {"mlb": {"cutoff": 0.55}, "nba": {"cutoff": 0.80}},
        "stats": {"mlb|hits": {"cutoff": 0.60}},
    }
    assert cc.rec_cutoff("mlb", "hits", table=table) == 0.60   # stat override
    assert cc.rec_cutoff("mlb", "rbis", table=table) == 0.55   # sport fallback
    assert cc.rec_cutoff("nba", "points", table=table) == 0.80
    assert cc.rec_cutoff("nhl", "goals", table=table) == 0.70  # global default
    assert cc.rec_cutoff(None, None, table=table) == 0.70
