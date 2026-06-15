"""Per-(sport, stat, direction) probability calibration from settled picks.

The per-model isotonic calibrators are fit on P(over) and are direction-SYMMETRIC
(``P_under = 1 - P_over``), so a model that's biased on one side stays biased —
e.g. MLB total_bases UNDER picks win ~45% while the logged probability says more,
because pulling P(over) down to make overs honest inflates P(under). This learns a
SEPARATE monotonic calibration per (sport, stat, direction) from the system's own
settled history (``model_prob_raw`` → realised win rate), so each side is made
honest on its own. Identity fallback wherever a cell has too little data, so every
other bucket behaves exactly as before.

It composes with selection: log_picks applies this to the raw prob before the
market blend, so the blended ``model_prob`` (which `rec_cutoff` selects on, and
which the per-direction cutoffs are themselves derived from) becomes honest and
the cutoffs auto-recompute around it. ``model_prob_raw`` is kept UNCORRECTED so a
re-fit always trains on the original model output.

Regenerate the committed seed:  python -m props.models.dir_calibration
"""
import json
from pathlib import Path

from sqlalchemy import text

from props.models.prob_calibration import _logit, _sigmoid
from props.utils.logging import log

_JSON_PATH = Path(__file__).with_name("dir_calibration.json")
MIN_N = 40                                  # settled picks needed in a cell to fit
_CACHE: dict | None = None


def _key(sport: str, stat: str, direction: str) -> str:
    return f"{sport}|{stat}|{direction}"


def fit_cell(probs, wins) -> dict | None:
    """Fit a 2-parameter Platt map (calibrated = sigmoid(a·logit(p)+b)) for one
    (sport,stat,direction) cell. Platt — not isotonic — because per-cell samples
    are small (~40-110) and isotonic overfits the thin tails into 0/1 locks;
    two params stay robust and smooth. Returns {a,b,n,status} or None."""
    import math
    n = len(probs)
    if n < MIN_N:
        return None
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        X = np.array([[_logit(float(p))] for p in probs])
        y = np.array([int(w) for w in wins])
        if y.min() == y.max():              # all wins or all losses — can't fit
            return None
        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit(X, y)
        a = float(lr.coef_[0][0])
        b = float(lr.intercept_[0])
        # a<=0 would be non-monotonic / flip the ranking — keep identity instead.
        if not math.isfinite(a) or not math.isfinite(b) or a <= 0:
            return None
        return {"a": a, "b": b, "n": n, "status": "fit"}
    except Exception as e:
        log.warning("dir_calib_fit_failed", error=str(e)[:120])
        return None


def calibrate_dir(sport: str, stat: str, direction: str, prob,
                  table: dict | None = None):
    """Map a raw model probability to its per-direction calibrated value.
    Identity when the cell isn't fit."""
    if prob is None:
        return prob
    table = table if table is not None else load_calibration()
    cell = table.get(_key(sport, stat, direction))
    if not cell or cell.get("status") != "fit":
        return float(prob)
    return _sigmoid(cell["a"] * _logit(float(prob)) + cell["b"])


def _fetch_rows(engine=None):
    from props.utils.db import engine as default_engine
    eng = engine or default_engine
    with eng.connect() as conn:
        return conn.execute(text("""
            SELECT g.sport_code AS sport, pk.stat_type AS stat, pk.direction AS dir,
                   pk.model_prob_raw::float AS p, (pk.leg_result = 'win')::int AS w
            FROM picks pk JOIN games g USING (game_id)
            WHERE pk.leg_result IN ('win', 'loss') AND pk.model_prob_raw IS NOT NULL
        """)).all()


def compute_from_db(engine=None) -> dict:
    from collections import defaultdict
    rows = _fetch_rows(engine)
    cells: dict = defaultdict(lambda: ([], []))
    for r in rows:
        xs, ws = cells[_key(r.sport, r.stat, r.dir)]
        xs.append(r.p)
        ws.append(r.w)
    out = {}
    for k, (xs, ws) in cells.items():
        fit = fit_cell(xs, ws)
        if fit:
            out[k] = fit
    return out


def load_calibration() -> dict:
    """Live per-direction maps from the DB, falling back to the committed seed,
    then to empty (= identity everywhere). Cached per process."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        live = compute_from_db()
        if live:
            _CACHE = live
            return _CACHE
    except Exception as e:
        log.warning("dir_calib_db_failed", error=str(e)[:120])
    _CACHE = json.loads(_JSON_PATH.read_text()) if _JSON_PATH.exists() else {}
    return _CACHE


def main():
    from props.utils.config import settings
    engine = None
    if settings.railway_database_url:
        from sqlalchemy import create_engine
        engine = create_engine(settings.railway_database_url)
        print("using RAILWAY DB (prod) for the committed seed")
    table = compute_from_db(engine)
    _JSON_PATH.write_text(json.dumps(table, indent=2) + "\n")
    print(f"wrote {_JSON_PATH}: {len(table)} calibrated cell(s)")
    for k, v in sorted(table.items()):
        eff = ", ".join(f"{x}->{calibrate_dir(*k.split('|'), x, table):.2f}"
                        for x in (0.55, 0.65, 0.75, 0.85))
        print(f"  {k:32} n={v['n']:4} a={v['a']:.2f} b={v['b']:+.2f}  {eff}")


if __name__ == "__main__":
    main()
