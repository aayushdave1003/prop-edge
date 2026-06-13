"""Self-tuning per-sport model/market blend weights.

A blended probability — ``w·model + (1−w)·market`` — beats either source alone,
with the weight differing sharply by sport because prop markets differ in
efficiency. Validated on settled picks with a *real* market line (2026-06-13,
n=412): NBA `w≈0.15` (the sharp market dominates; Brier 0.278→0.250) and MLB
`w≈0.75` (the model already beats the softer market; 0.233→0.231).

This module learns ``w`` per sport from the system's OWN settled history —
the grid value that minimises the Brier score of the blend — recomputed from
the live DB (like the cutoffs and the Platt recalibration), with a committed
JSON seed and an identity (`w=1.0`, pure model) fallback when there isn't enough
real-line data yet.

CRITICAL: only ever blend against a REAL market line. The market probability
must be ``None`` when no line exists — never the 0.5 neutral prior, which would
silently drag every probability toward a coin flip (see
[[market-edge-is-neutral-prior]]). ``blend()`` returns the pure model prob when
``market_prob is None``.

Regenerate the seed:  python -m props.models.blend_weights
"""
import json
from pathlib import Path

from sqlalchemy import text

from props.utils.logging import log

_JSON_PATH = Path(__file__).with_name("blend_weights.json")
MIN_N_BLEND = 40          # per sport: below this, stay pure-model (w=1.0)
DEFAULT_W = 1.0           # pure model when untuned
GRID = [i / 20 for i in range(21)]   # 0.00 .. 1.00 step 0.05
_CACHE: dict | None = None


def blend(sport: str, model_prob: float, market_prob, weights: dict | None = None) -> float:
    """Blend a model probability with the market's no-vig probability for the
    SAME side. Returns the pure model prob when there's no real market line."""
    if model_prob is None:
        return model_prob
    if market_prob is None:                 # no real line — never blend on a prior
        return float(model_prob)
    if weights is None:
        weights = load_weights()
    w = float(weights.get("sports", {}).get(sport, weights.get("default_w", DEFAULT_W)))
    return w * float(model_prob) + (1.0 - w) * float(market_prob)


def _brier(rows, w) -> float:
    # rows: (model_prob, market_implied_for_side, win)
    return sum((w * mp + (1 - w) * mi - y) ** 2 for mp, mi, y in rows) / len(rows)


def fit_weights(by_sport: dict) -> dict:
    """by_sport: {sport: [(model_prob, market_implied, win), ...]}. Returns the
    weights table: per-sport best-Brier w (>= MIN_N_BLEND), else pure model."""
    sports = {}
    for sport, rows in sorted(by_sport.items()):
        if len(rows) < MIN_N_BLEND:
            sports[sport] = {"w": DEFAULT_W, "n": len(rows), "status": "untuned"}
            continue
        best_w = min(GRID, key=lambda w: _brier(rows, w))
        sports[sport] = {
            "w": best_w, "n": len(rows),
            "brier": round(_brier(rows, best_w), 4),
            "brier_model_only": round(_brier(rows, 1.0), 4),
            "status": "tuned",
        }
    return {"default_w": DEFAULT_W, "min_n": MIN_N_BLEND, "sports": sports}


def _fetch_rows(engine=None) -> dict:
    from props.utils.db import engine as default_engine
    eng = engine or default_engine
    with eng.connect() as conn:
        raw = conn.execute(text("""
            SELECT pk.sport_code, pk.direction, pk.model_prob::float AS mp,
                   AVG(mo.market_over_prob)::float AS mkt_over,
                   (pk.leg_result = 'win')::int AS win
            FROM picks pk
            JOIN market_odds mo
              ON mo.player_id = pk.player_id AND mo.game_id = pk.game_id
            WHERE pk.leg_result IN ('win', 'loss') AND mo.market_over_prob IS NOT NULL
            GROUP BY pk.pick_id, pk.sport_code, pk.direction, pk.model_prob, pk.leg_result
        """)).all()
    by_sport: dict = {}
    for r in raw:
        implied = r.mkt_over if r.direction == "over" else 1 - r.mkt_over
        by_sport.setdefault(r.sport_code, []).append((r.mp, implied, r.win))
    return by_sport


def compute_from_db(engine=None) -> dict:
    return fit_weights(_fetch_rows(engine))


def load_weights() -> dict:
    """Live per-sport weights from the DB, falling back to the seed JSON, then
    the pure-model default. Cached per process."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        tbl = compute_from_db()
        # build the simple {sports: {sport: w}} shape blend() expects
        _CACHE = {"default_w": DEFAULT_W,
                  "sports": {s: v["w"] for s, v in tbl["sports"].items()},
                  "detail": tbl["sports"]}
        # prefer the seed only if the DB produced nothing tuned
        if not any(v.get("status") == "tuned" for v in tbl["sports"].values()) \
                and _JSON_PATH.exists():
            _CACHE = json.loads(_JSON_PATH.read_text())
        return _CACHE
    except Exception:
        if _JSON_PATH.exists():
            _CACHE = json.loads(_JSON_PATH.read_text())
        else:
            _CACHE = {"default_w": DEFAULT_W, "sports": {}}
        return _CACHE


def main():
    from props.utils.config import settings
    engine = None
    if settings.railway_database_url:
        from sqlalchemy import create_engine
        engine = create_engine(settings.railway_database_url)
        print("using RAILWAY DB (prod) for the committed seed")
    tbl = compute_from_db(engine)
    seed = {"default_w": DEFAULT_W,
            "sports": {s: v["w"] for s, v in tbl["sports"].items()},
            "detail": tbl["sports"]}
    _JSON_PATH.write_text(json.dumps(seed, indent=2) + "\n")
    print(f"wrote {_JSON_PATH}")
    for s, v in tbl["sports"].items():
        print(f"  {s:5s} w={v['w']:.2f} ({int(v['w']*100)}% model)  n={v['n']}  "
              f"status={v['status']}" +
              (f"  Brier {v['brier_model_only']}→{v['brier']}" if v["status"] == "tuned" else ""))


if __name__ == "__main__":
    main()
