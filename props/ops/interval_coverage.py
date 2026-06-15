"""Prediction-interval coverage check — are the displayed intervals honest?

The dashboard shows a Poisson 25-75% interval around each prediction (from
predicted_mean). If the model is well-calibrated, ~50% of actual outcomes should
land inside it (and ~80% inside 10-90%). This measures the EMPIRICAL coverage on
settled predictions and flags a stat whose intervals are miscalibrated (too
narrow → overconfident, or too wide). Per stat + overall.

Run:  python -m props.ops.interval_coverage          (alerts on miscalibration)
      python -m props.ops.interval_coverage --quiet   (print only)
"""
from __future__ import annotations

import argparse

import pandas as pd
from scipy.stats import poisson
from sqlalchemy import text

from props.utils.db import engine, db_banner
from props.utils.config import settings
from props.utils.logging import log, configure_logging

LEVELS = [(0.25, 0.75, 0.50), (0.10, 0.90, 0.80)]
TOL = 0.10          # flag if empirical coverage is >10pp off nominal
MIN_N = 50          # need this many settled predictions to judge


def _data() -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT pr.stat_type AS stat, pr.predicted_mean::float AS mean,
               (pg.stats->>pr.stat_type)::float AS actual
        FROM predictions pr
        JOIN player_games pg ON pg.player_id = pr.player_id AND pg.game_id = pr.game_id
        JOIN games g ON g.game_id = pr.game_id
        WHERE g.status = 'final' AND pr.predicted_mean IS NOT NULL
          AND (pg.stats ->> pr.stat_type) IS NOT NULL
    """), engine)


def _coverage(df: pd.DataFrame, lo_q: float, hi_q: float) -> float:
    lo = poisson.ppf(lo_q, df["mean"])
    hi = poisson.ppf(hi_q, df["mean"])
    return float(((df["actual"] >= lo) & (df["actual"] <= hi)).mean())


def run_checks() -> list[dict]:
    df = _data()
    if len(df) < MIN_N:
        return [{"level": "ok", "name": "interval_coverage",
                 "detail": f"only {len(df)} settled predictions — too few to judge"}]
    findings: list[dict] = []
    for lo_q, hi_q, nominal in LEVELS:
        cov = _coverage(df, lo_q, hi_q)
        off = abs(cov - nominal)
        lvl = "warn" if off > TOL else "ok"
        findings.append({"level": lvl, "name": f"coverage_{int(lo_q*100)}_{int(hi_q*100)}",
                         "detail": f"{int(lo_q*100)}-{int(hi_q*100)}% interval covers "
                                   f"{cov:.0%} of outcomes (target {nominal:.0%}, n={len(df)})"})
    # per-stat 25-75 detail (only where there's enough data)
    weak = []
    for stat, g in df.groupby("stat"):
        if len(g) >= MIN_N:
            cov = _coverage(g, 0.25, 0.75)
            if abs(cov - 0.50) > TOL:
                weak.append(f"{stat} {cov:.0%}")
    if weak:
        findings.append({"level": "ok", "name": "coverage_by_stat",
                         "detail": "25-75% off-target for: " + ", ".join(weak)})
    return findings


def _alert(findings: list[dict]):
    warns = [f for f in findings if f["level"] == "warn"]
    if not warns or not settings.discord_webhook_url:
        return
    import requests
    lines = "\n".join(f"• **{f['name']}** — {f['detail']}" for f in warns)
    try:
        requests.post(settings.discord_webhook_url, json={"embeds": [{
            "title": "⚠️ prop-edge interval coverage",
            "description": f"Prediction intervals are miscalibrated:\n{lines}",
            "color": 0xE8A317, "footer": {"text": "interval_coverage"}}]}, timeout=10)
    except Exception as e:
        log.warning("interval_coverage_alert_failed", error=str(e)[:120])


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord")
    args = ap.parse_args()
    print(db_banner())
    findings = run_checks()
    for f in findings:
        print(f"  {'⚠️ ' if f['level'] == 'warn' else '✓ '}{f['name']}: {f['detail']}")
    if not args.quiet:
        _alert(findings)
    log.info("interval_coverage", warnings=sum(f["level"] == "warn" for f in findings))


if __name__ == "__main__":
    main()
