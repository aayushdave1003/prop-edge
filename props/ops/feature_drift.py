"""Feature-drift monitor — catches a model feature that silently stopped populating.

A model can't tell you when an upstream ingest breaks and a feature it leans on
goes to all-zeros (the silent-signal-break failure mode — cf. the weather/SoS
features that come from separate ingest steps). For each MLB model this compares
every feature's RECENT population rate to its baseline and flags a feature the
model WEIGHTS HEAVILY (high gain) whose coverage collapsed — i.e. the model is
flying blind on one of its real drivers. Also surfaces the top drivers per model
for visibility.

Run:  python -m props.ops.feature_drift          (alerts on collapses)
      python -m props.ops.feature_drift --quiet  (print only, no Discord)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from sqlalchemy import text

from props.utils.db import engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging

MLB_MODELS = ["total_bases_v1", "hits_v1", "mlb_home_runs_v1", "rbis_v1"]
RECENT_DAYS = 14
BASE_DAYS = 60
COLLAPSE_FRAC = 0.4        # recent coverage < 40% of baseline = collapsed
MIN_GAIN_SHARE = 0.01      # only care about features the model actually uses
MIN_BASE_COV = 0.5         # only flag features that USED to be well-populated


def _gains(model_name: str) -> dict[str, float]:
    path = Path("models") / f"{model_name}.txt"
    if not path.exists():
        return {}
    b = lgb.Booster(model_file=str(path))
    g = b.feature_importance(importance_type="gain")
    tot = float(g.sum()) or 1.0
    return {k: float(gv) / tot for k, gv in zip(b.feature_name(), g)}


def _derived(days_from: int, days_to: int) -> pd.DataFrame:
    """MLB batter-games' derived JSONB in [days_from, days_to) days ago."""
    df = pd.read_sql(text("""
        SELECT pg.derived FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code = 'mlb' AND (pg.stats->>'plate_appearances')::int > 0
          AND g.game_date >= CURRENT_DATE - :a AND g.game_date < CURRENT_DATE - :b
    """), engine, params={"a": days_from, "b": days_to})
    return pd.json_normalize(df["derived"]) if not df.empty else pd.DataFrame()


def _cov(df: pd.DataFrame, key: str) -> float:
    if df.empty or key not in df.columns:
        return 0.0
    return float((pd.to_numeric(df[key], errors="coerce").fillna(0) != 0).mean())


def run_checks() -> list[dict]:
    recent = _derived(RECENT_DAYS, 0)
    base = _derived(BASE_DAYS, RECENT_DAYS)
    if recent.empty or base.empty:
        return [{"level": "ok", "name": "feature_drift",
                 "detail": "insufficient recent MLB data to assess drift"}]

    findings: list[dict] = []
    flagged: set[str] = set()
    for model in MLB_MODELS:
        for key, share in _gains(model).items():
            if share < MIN_GAIN_SHARE or key in flagged:
                continue
            bc, rc = _cov(base, key), _cov(recent, key)
            if bc >= MIN_BASE_COV and rc < COLLAPSE_FRAC * bc:
                findings.append({"level": "warn", "name": "feature_collapsed",
                                 "detail": f"{key} coverage {bc:.0%}→{rc:.0%} "
                                           f"(model {model}, gain {share:.0%}) — upstream "
                                           "feature likely broken"})
                flagged.add(key)

    if not findings:
        top = sorted(_gains("total_bases_v1").items(), key=lambda x: -x[1])[:4]
        findings.append({"level": "ok", "name": "feature_drift",
                         "detail": "no collapsed features; top total_bases drivers — "
                                   + ", ".join(f"{k} {s:.0%}" for k, s in top)})
    return findings


def _alert(findings: list[dict]):
    warns = [f for f in findings if f["level"] == "warn"]
    if not warns or not settings.discord_webhook_url:
        return
    import requests
    lines = "\n".join(f"• **{f['name']}** — {f['detail']}" for f in warns)
    payload = {"embeds": [{
        "title": "⚠️ prop-edge feature drift",
        "description": f"{len(warns)} feature(s) the models rely on went sparse:\n{lines}",
        "color": 0xE8A317, "footer": {"text": "feature_drift"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("feature_drift_alert_failed", error=str(e)[:120])


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord")
    args = ap.parse_args()
    findings = run_checks()
    for f in findings:
        print(f"  {'⚠️ ' if f['level'] == 'warn' else '✓ '}{f['name']}: {f['detail']}")
    warns = [f for f in findings if f["level"] == "warn"]
    log.info("feature_drift", checks=len(findings), warnings=len(warns))
    if not args.quiet:
        _alert(findings)
    return warns


if __name__ == "__main__":
    main()
