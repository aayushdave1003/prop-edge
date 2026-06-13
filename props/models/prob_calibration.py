"""Self-tuning probability recalibration (Platt scaling) from settled picks.

The per-stat models carry isotonic calibration fit on regular-season data, but
the live slate drifts (playoff NBA is a different distribution), leaving the
logged probabilities **over-confident**: across settled picks every band above
~0.60 predicts higher than it actually wins (e.g. 0.80–0.90 predicted 83% but
won 62%). That inflates Kelly stakes and the confidence we show.

This module learns a 2-parameter **Platt** correction from the system's OWN
settled history — ``calibrated = sigmoid(a · logit(model_prob) + b)`` — and
recomputes it from the live DB (like the per-category cutoffs do), with a
committed JSON seed as the offline fallback and an identity map when there isn't
enough data yet. ``a < 1`` shrinks the over-confident extremes toward the base
rate; two parameters stay robust on the few hundred settled picks we have, where
isotonic would overfit the thin upper tail.

Selection is deliberately left on the RAW probability + empirical cutoffs (a
monotonic transform doesn't change the ordering, and the cutoffs are derived
from realised win rates, so they're already calibration-proof). Recalibration is
applied only where the probability's *magnitude* matters: Kelly sizing and the
confidence shown to a human.

Regenerate the seed:  python -m props.models.prob_calibration
"""
import json
import math
from pathlib import Path

from sqlalchemy import text

from props.utils.logging import log

_JSON_PATH = Path(__file__).with_name("prob_calibration.json")
MIN_N_CALIB = 100          # below this, don't trust a fit — fall back to identity
IDENTITY = {"a": 1.0, "b": 0.0, "n": 0, "status": "identity"}
_EPS = 1e-6
_CACHE: dict | None = None


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


def fit_platt(probs, wins) -> dict:
    """Fit calibrated = sigmoid(a·logit(p) + b) via logistic regression.

    ``probs`` / ``wins`` are equal-length sequences (model_prob, 0/1). Returns
    {a, b, n, status}. Falls back to identity when there's too little data."""
    pairs = [(float(p), int(w)) for p, w in zip(probs, wins)]
    n = len(pairs)
    if n < MIN_N_CALIB:
        return {**IDENTITY, "n": n}
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        X = np.array([[_logit(p)] for p, _ in pairs])
        y = np.array([w for _, w in pairs])
        if y.min() == y.max():               # all wins or all losses — can't fit
            return {**IDENTITY, "n": n}
        # ~unregularised so we recover the true Platt slope, not a shrunk one.
        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit(X, y)
        a = float(lr.coef_[0][0])
        b = float(lr.intercept_[0])
        # Guard against a degenerate/inverted fit (a<=0 would be non-monotonic
        # or flip the ranking) — keep identity rather than ship something worse.
        if not math.isfinite(a) or not math.isfinite(b) or a <= 0:
            return {**IDENTITY, "n": n}
        return {"a": a, "b": b, "n": n, "status": "fit"}
    except Exception as e:                    # sklearn/numpy missing or solver blew up
        log.warning("platt_fit_failed", error=str(e)[:120])
        return {**IDENTITY, "n": n}


def calibrate(prob, params: dict | None = None) -> float:
    """Map a raw model probability to its recalibrated value."""
    if prob is None:
        return prob
    if params is None:
        params = load_calibration()
    if params.get("status") == "identity":
        return float(prob)
    return _sigmoid(params["a"] * _logit(float(prob)) + params["b"])


def _fetch_rows(engine=None):
    from props.utils.db import engine as default_engine
    eng = engine or default_engine
    with eng.connect() as conn:
        return conn.execute(text("""
            SELECT model_prob::float AS p, (leg_result = 'win')::int AS w
            FROM picks
            WHERE leg_result IN ('win', 'loss') AND model_prob IS NOT NULL
        """)).all()


def compute_from_db(engine=None) -> dict:
    rows = _fetch_rows(engine)
    return fit_platt([r.p for r in rows], [r.w for r in rows])


def load_calibration() -> dict:
    """Live Platt params from the DB, falling back to the seed JSON, then
    identity. Cached for the process (cleared per dashboard cache TTL)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        _CACHE = compute_from_db()
        if _CACHE.get("status") == "identity" and _JSON_PATH.exists():
            seed = json.loads(_JSON_PATH.read_text())
            # only prefer the seed if it actually carries a fit
            if seed.get("status") == "fit":
                _CACHE = seed
        return _CACHE
    except Exception:
        if _JSON_PATH.exists():
            _CACHE = json.loads(_JSON_PATH.read_text())
        else:
            _CACHE = dict(IDENTITY)
        return _CACHE


def main():
    from props.utils.config import settings
    engine = None
    if settings.railway_database_url:
        from sqlalchemy import create_engine
        engine = create_engine(settings.railway_database_url)
        print("using RAILWAY DB (prod) for the committed seed")
    params = compute_from_db(engine)
    _JSON_PATH.write_text(json.dumps(params, indent=2) + "\n")
    print(f"wrote {_JSON_PATH}: {params}")
    if params["status"] == "fit":
        for x in (0.55, 0.625, 0.70, 0.80, 0.90, 0.97):
            print(f"  {x:.3f} -> {calibrate(x, params):.3f}")


if __name__ == "__main__":
    main()
