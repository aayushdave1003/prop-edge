"""'Why this pick' — per-prediction feature contributions via LightGBM SHAP.

LightGBM's `pred_contrib=True` returns exact per-prediction SHAP values for tree
models, cheaply. `top_drivers()` runs it on one feature vector and returns the
features that pushed the projection up/down the most, so a pick reads as "↑ recent
total bases · ↑ park · ↓ opposing K-rate" instead of an opaque number.
"""
import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

MODEL_DIR = Path("models")

# Friendly labels — longest prefixes first so "last_10_avg_" wins over "last_".
_PREFIX = [
    ("last_5_avg_", "recent "), ("last_10_avg_", "recent "), ("last_20_avg_", "form "),
    ("season_avg_", "season "), ("last_10_rate_over_", "rate over "),
]
_EXACT = {
    "park_factor": "ballpark", "days_rest": "rest", "platoon_advantage": "platoon edge",
    "games_played_season": "games played", "wx_temp": "temperature", "wx_wind_out": "wind blowing out",
    "bat_order_spot": "lineup spot", "last_10_avg_bat_order_spot": "lineup spot",
    "last_10_avg_faced_era": "quality of pitchers faced", "last_10_avg_faced_k_rate": "K-rate of pitchers faced",
}
_STAT = {"total_bases": "total bases", "home_runs": "home runs", "at_bats": "at-bats",
         "rbis": "RBIs", "strikeouts": "strikeouts", "batter_iso": "power (ISO)",
         "batter_slg": "slugging", "batter_hard_contact": "hard contact", "batter_k_rate": "K-rate"}


def _label(key: str) -> str:
    if key in _EXACT:
        return _EXACT[key]
    if key.startswith("pitcher_"):  # opposing-pitcher quality keys
        tail = key.replace("pitcher_last_5_", "").replace("pitcher_last_10_", "")
        return "opp pitcher " + tail.replace("_", " ")
    for pre, human in _PREFIX:
        if key.startswith(pre):
            rest = key[len(pre):]
            return human + _STAT.get(rest, rest.replace("_", " "))
    return key.replace("_", " ")


def top_drivers(model_name: str, feature_dict: dict, k: int = 4) -> list[tuple[str, float]]:
    """Top-k features by absolute SHAP contribution for this feature vector.
    Returns [(feature_key, signed_contribution)], biggest magnitude first."""
    meta_path = MODEL_DIR / f"{model_name}_meta.json"
    model_path = MODEL_DIR / f"{model_name}.txt"
    if not (meta_path.exists() and model_path.exists()):
        return []
    keys = json.loads(meta_path.read_text())["feature_keys"]
    booster = lgb.Booster(model_file=str(model_path))
    X = pd.DataFrame([{key: float(feature_dict.get(key, 0) or 0) for key in keys}])
    contrib = booster.predict(X, pred_contrib=True)[0]   # n_features + 1 (last = base)
    pairs = sorted(zip(keys, contrib[:-1]), key=lambda kv: -abs(kv[1]))
    return [(key, float(v)) for key, v in pairs[:k] if abs(v) > 1e-6]


def format_drivers(drivers: list[tuple[str, float]], k: int = 4) -> str:
    """Human one-liner: '↑ recent total bases · ↓ opp pitcher K-rate'. Dedupes by
    label (last_5/last_10 both read 'recent …') and keeps the k strongest."""
    seen: set[str] = set()
    parts: list[str] = []
    for key, v in drivers:
        lab = _label(key)
        if lab in seen:
            continue
        seen.add(lab)
        parts.append(f"{'↑' if v > 0 else '↓'} {lab}")
        if len(parts) >= k:
            break
    return " · ".join(parts)


def explain(model_name: str, feature_dict: dict, k: int = 4) -> str:
    # pull extra raw drivers so dedup-by-label still yields k distinct ones
    return format_drivers(top_drivers(model_name, feature_dict, k * 3), k)
