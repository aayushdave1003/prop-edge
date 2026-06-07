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
