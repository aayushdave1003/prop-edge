"""Empirical interval calibration — honest 25-75% display ranges.

The dashboard's Poisson interval is mis-calibrated for some stats (hits' 25-75%
band actually covers ~79%, way too wide). Rather than ship feature-level quantile
models (which run too narrow on low-count stats — see quantile_intervals), this
fits the SIMPLE, honest-by-construction thing: bin the model's predicted_mean and
record the EMPIRICAL 25th/75th percentile of the actual outcome in each bin. The
displayed range is then the real central-50% of outcomes for that projection.

`empirical_interval(stat, mean)` returns (lo, hi) for a calibrated stat, else
None (the dashboard falls back to Poisson). Self-tuning from the DB with a
committed JSON seed.

Regenerate:  python -m props.models.interval_calibration
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pandas as pd

from props.utils.logging import log

_JSON_PATH = Path(__file__).with_name("interval_calibration.json")
SPLIT = pd.Timestamp("2025-01-01")
N_BINS = 8
STATS = {                                   # stat -> (training module, model stem)
    "hits": ("props.models.hits_v1", "hits_v1"),
    "total_bases": ("props.models.total_bases_v1", "total_bases_v1"),
}
_CACHE: dict | None = None


def compute_one(stat: str) -> list[dict]:
    import lightgbm as lgb
    mod = importlib.import_module(STATS[stat][0])
    df = mod.load_training_data()
    test = df[df["game_date"] >= SPLIT]
    booster = lgb.Booster(model_file=str(Path("models") / f"{STATS[stat][1]}.txt"))
    dfm = pd.DataFrame({"mean": booster.predict(test[mod.FEATURE_KEYS]),
                        "actual": test["y"].to_numpy(dtype=float)})
    dfm["bin"] = pd.qcut(dfm["mean"], N_BINS, duplicates="drop")
    anchors = []
    for _, g in dfm.groupby("bin", observed=True):
        anchors.append({"mean": round(float(g["mean"].mean()), 3),
                        "q25": float(g["actual"].quantile(0.25)),
                        "q75": float(g["actual"].quantile(0.75))})
    return sorted(anchors, key=lambda a: a["mean"])


def empirical_interval(stat: str, mean: float):
    """(lo, hi) integer range for a calibrated stat, or None to fall back to Poisson."""
    if stat is None:
        return None
    table = load()
    anchors = table.get(stat)
    if not anchors:
        return None
    best = min(anchors, key=lambda a: abs(a["mean"] - float(mean)))
    return int(round(best["q25"])), int(round(best["q75"]))


def load() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_JSON_PATH.read_text()) if _JSON_PATH.exists() else {}
    return _CACHE


def main():
    # Run against prod with: DATABASE_URL=$RAILWAY_DATABASE_URL python -m ...
    from props.utils.logging import configure_logging
    from props.utils.db import db_banner
    configure_logging()
    print(db_banner())
    table = {stat: compute_one(stat) for stat in STATS}
    _JSON_PATH.write_text(json.dumps(table, indent=2) + "\n")
    print(f"wrote {_JSON_PATH}")
    for stat, anchors in table.items():
        log.info("interval_calibration", stat=stat, bins=len(anchors))
        for a in anchors:
            print(f"  {stat:<12} mean≈{a['mean']:<5} → {int(round(a['q25']))}–{int(round(a['q75']))}")


if __name__ == "__main__":
    main()
