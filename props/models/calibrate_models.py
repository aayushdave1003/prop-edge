"""Generalized isotonic calibration for the prop models that lack a calibrator (E7).

Same proven approach as calibrate_nba.py (time-ordered out-of-fold Poisson preds
-> IsotonicRegression mapping raw prob -> empirical hit rate), but config-driven
so it covers NBA threes, WNBA, and MLB. NHL is deliberately excluded — only ~440
historical player-games, far too few to calibrate honestly.

A calibrator is only saved if there's enough data AND the resulting map isn't
degenerate (didn't collapse to the clip bounds).

Run: python -m props.models.calibrate_models [--force]
"""
import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy import stats as scipy_stats
from sklearn.isotonic import IsotonicRegression
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import log, configure_logging

# played(stats) -> bool keeps cold/DNP rows out. target = key in player_games.stats.
CONFIGS = [
    {"name": "nba_threes_made_v1", "sport": "nba", "target": "fg3_made",
     "lines": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
     "played": lambda s: float(s.get("minutes", 0) or 0) >= 10},

    {"name": "wnba_points_v1", "sport": "wnba", "target": "points",
     "lines": [5.5, 9.5, 12.5, 14.5, 17.5, 19.5, 22.5, 24.5],
     "played": lambda s: float(s.get("minutes", 0) or 0) >= 10},
    {"name": "wnba_rebounds_v1", "sport": "wnba", "target": "rebounds",
     "lines": [2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 9.5],
     "played": lambda s: float(s.get("minutes", 0) or 0) >= 10},
    {"name": "wnba_assists_v1", "sport": "wnba", "target": "assists",
     "lines": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5],
     "played": lambda s: float(s.get("minutes", 0) or 0) >= 10},

    {"name": "strikeouts_v1", "sport": "mlb", "target": "strikeouts_pitcher",
     "lines": [3.5, 4.5, 5.5, 6.5, 7.5, 8.5],
     "played": lambda s: float(s.get("outs_recorded", 0) or 0) > 0},
    {"name": "rbis_v1", "sport": "mlb", "target": "rbis",
     "lines": [0.5, 1.5, 2.5],
     "played": lambda s: float(s.get("at_bats", 0) or 0) > 0},
    {"name": "total_bases_v1", "sport": "mlb", "target": "total_bases",
     "lines": [0.5, 1.5, 2.5, 3.5],
     "played": lambda s: float(s.get("at_bats", 0) or 0) > 0},
    {"name": "mlb_home_runs_v1", "sport": "mlb", "target": "home_runs",
     "lines": [0.5, 1.5],
     "played": lambda s: float(s.get("at_bats", 0) or 0) > 0},
    {"name": "strikeouts_batter_v1", "sport": "mlb", "target": "strikeouts",
     "lines": [0.5, 1.5, 2.5],
     "played": lambda s: float(s.get("at_bats", 0) or 0) > 0},
    {"name": "hits_runs_rbis_v1", "sport": "mlb", "target": ["hits", "runs", "rbis"],
     "lines": [1.5, 2.5, 3.5, 4.5],
     "played": lambda s: float(s.get("at_bats", 0) or 0) > 0},
]

# NBA/WNBA combo markets — summed targets, rotation-player filter (minutes >= 10)
_COMBO_COMPONENTS = {"pts_rebs_asts": ["points", "rebounds", "assists"],
                     "pts_rebs": ["points", "rebounds"], "pts_asts": ["points", "assists"],
                     "rebs_asts": ["rebounds", "assists"]}
_COMBO_LINES = {"pts_rebs_asts": [9.5, 14.5, 19.5, 24.5, 29.5, 34.5],
                "pts_rebs": [7.5, 11.5, 15.5, 19.5, 23.5],
                "pts_asts": [7.5, 11.5, 15.5, 19.5, 23.5],
                "rebs_asts": [3.5, 5.5, 7.5, 9.5, 11.5]}
CONFIGS += [
    {"name": f"{sp}_{combo}_v1", "sport": sp, "target": _COMBO_COMPONENTS[combo],
     "lines": _COMBO_LINES[combo],
     "played": lambda s: float(s.get("minutes", 0) or 0) >= 10}
    for sp in ("nba", "wnba") for combo in _COMBO_COMPONENTS
]

MIN_POINTS = 400          # need this many (raw_prob, hit) pairs to trust the fit
MIN_SPREAD = 0.03         # calibrated values must vary at least this much (not collapsed)


def calibrate_one(cfg: dict, force: bool) -> str:
    name = cfg["name"]
    model_path = Path(f"models/{name}.txt")
    meta_path = Path(f"models/{name}_meta.json")
    out_path = Path(f"models/{name}_calibrator.pkl")
    if out_path.exists() and not force:
        return f"{name}: already calibrated (skip; --force to redo)"
    if not model_path.exists() or not meta_path.exists():
        return f"{name}: model/meta missing (skip)"

    meta = json.loads(meta_path.read_text())
    feature_keys = meta["feature_keys"]

    df = pd.read_sql(text("""
        SELECT pg.derived, pg.stats, g.game_date
        FROM player_games pg JOIN games g USING(game_id)
        WHERE g.sport_code = :sport AND g.status = 'final'
          AND g.game_date < CURRENT_DATE AND pg.derived IS NOT NULL
        ORDER BY g.game_date
    """), engine, params={"sport": cfg["sport"]})
    if df.empty:
        return f"{name}: no rows (skip)"

    played_mask = df["stats"].apply(lambda s: cfg["played"](s or {}))
    df = df[played_mask].reset_index(drop=True)
    if len(df) < 200:
        return f"{name}: only {len(df)} played rows — too few, SKIP (honest)"

    derived = pd.json_normalize(df["derived"])
    stats = pd.json_normalize(df["stats"])
    X = pd.DataFrame({k: pd.to_numeric(derived[k], errors="coerce").fillna(0)
                      if k in derived.columns else 0.0 for k in feature_keys}).astype(float)
    tgt = cfg["target"]   # str (single box-score key) or list (combo = sum of keys)
    if isinstance(tgt, (list, tuple)):
        actual = sum(pd.to_numeric(stats[t], errors="coerce").fillna(0) for t in tgt).values
    else:
        actual = pd.to_numeric(stats[tgt], errors="coerce").fillna(0).values

    n, folds = len(X), 5
    fold_size = n // folds
    raw_probs, actual_hit = [], []
    for fold in range(folds):
        vs = fold * fold_size
        ve = vs + fold_size if fold < folds - 1 else n
        tr = list(range(0, vs))
        if len(tr) < 100:
            continue
        fm = lgb.train(
            {"objective": "poisson", "metric": "poisson", "learning_rate": 0.05,
             "num_leaves": 20, "verbose": -1, "seed": 42},
            lgb.Dataset(X.iloc[tr], actual[tr]),
            num_boost_round=meta.get("clf_best_iter", 200) or 200)
        lam = fm.predict(X.iloc[vs:ve])
        for line in cfg["lines"]:
            raw_probs.extend((1 - scipy_stats.poisson.cdf(int(line), lam)).tolist())
            actual_hit.extend((actual[vs:ve] > line).astype(float).tolist())

    raw_probs, actual_hit = np.array(raw_probs), np.array(actual_hit)
    if len(raw_probs) < MIN_POINTS:
        return f"{name}: only {len(raw_probs)} calibration points (<{MIN_POINTS}) — SKIP"

    order = np.argsort(raw_probs)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99, increasing=True)
    iso.fit(raw_probs[order], actual_hit[order])

    pts = np.array([0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
    cal = iso.predict(pts)
    # Reject degenerate maps: (a) collapsed to a flat ceiling — too little
    # dynamic range (e.g. WNBA assists -> ~33%), or (b) a tail pinned at the
    # upper clip when that tail is SPARSE (overfit, e.g. HR 60%->99% on a handful
    # of points). A pinned-high tail backed by many points is legitimate — easy
    # combo/batter-K lines genuinely hit ~99% — so don't reject those.
    n_tail = int((raw_probs >= 0.85).sum())
    collapsed = (cal[-1] - cal[0]) < 0.15
    sparse_pinned = cal.max() >= 0.97 and n_tail < 300
    if collapsed or sparse_pinned:
        return (f"{name}: degenerate map (max={cal.max():.2f}, "
                f"range={cal[-1]-cal[0]:.2f}, tail_n={n_tail}) — SKIP, not saving")

    with open(out_path, "wb") as f:
        pickle.dump({"global": iso}, f)
    mapping = "  ".join(f"{r:.0%}->{c:.0%}" for r, c in zip(pts, cal))
    log.info("calibrator_saved", model=name, points=len(raw_probs))
    return f"{name}: SAVED ({len(df):,} rows, {len(raw_probs):,} pts)  {mapping}"


def main():
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="recalibrate even if a calibrator exists")
    args = p.parse_args()
    print("=== Calibration (NHL skipped: too few games) ===")
    for cfg in CONFIGS:
        print(" ", calibrate_one(cfg, args.force))


if __name__ == "__main__":
    main()
