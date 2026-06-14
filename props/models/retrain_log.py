"""Record each model retrain as a backtest_runs row (the Performance tab charts
the history). For regression models the metric is MAE improvement over the
season-average baseline — positive = the model beats just-predict-the-average."""
from datetime import date

from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log


def log_retrain_run(model_name: str, sport: str, since_date,
                    n_test: int, improvement_pct: float | None) -> None:
    """improvement_pct: how much the model beats its naive baseline on the test
    set (MAE for regression, log-loss for classifiers — positive is better)."""
    imp = round(improvement_pct, 2) if improvement_pct is not None else None
    try:
        with session_scope() as s:
            s.execute(text("""
                INSERT INTO backtest_runs
                    (run_at, sport, since_date, n_picks, mae_improvement_pct, trigger)
                VALUES (NOW(), :sp, :since, :n, :imp, :trig)
            """), {"sp": sport, "since": since_date, "n": int(n_test),
                   "imp": imp, "trig": f"retrain:{model_name}"})
        log.info("retrain_logged", model=model_name, n_test=int(n_test),
                 mae_improvement_pct=imp)
    except Exception as e:                  # never let logging break a retrain
        log.warning("retrain_log_failed", model=model_name, error=str(e)[:120])
