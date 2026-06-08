"""Ingest health monitor — catches a silently broken data pipeline.

Picks can quietly go to zero (or stale) when an *upstream* ingest breaks: the
PrizePicks scrape returns nothing, box scores stop landing (so nothing settles),
or the injury feed goes cold. daily.sh already alerts on 0 picks, but that fires
*after* the damage. This checks the ingest tables directly and pings Discord the
moment something looks off:

  - prop_lines: lines scraped recently + today's slate isn't anomalously thin
  - box scores: recent FINAL games all have player_games (else settling stalls)
  - injuries: the feed refreshed in the last ~40h

Run:  python -m props.maintenance.ingest_monitor          (alerts on warnings)
      python -m props.maintenance.ingest_monitor --quiet  (no Discord, just print)
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from props.utils.db import engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging

LINES_STALE_HOURS = 18      # refresh.yml scrapes ~3x/day; >18h = scrape broken
LINES_THIN_FRAC = 0.30      # today < 30% of the 7-day avg distinct lines = thin
LINES_MIN_BASELINE = 50     # only flag "thin" once there's a real baseline
INJURY_STALE_HOURS = 40     # injuries refresh daily; >40h = feed cold


def _scalar(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def run_checks() -> list[dict]:
    """Return a list of findings: {level: 'ok'|'warn', name, detail}."""
    findings: list[dict] = []
    with engine.connect() as c:
        # ── prop_lines: freshness + slate volume ─────────────────────────────
        hrs = _scalar(c, "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(snapshot_at)))/3600 "
                         "FROM prop_lines")
        if hrs is None:
            findings.append({"level": "warn", "name": "prop_lines",
                             "detail": "no prop_lines at all"})
        elif hrs > LINES_STALE_HOURS:
            findings.append({"level": "warn", "name": "prop_lines_stale",
                             "detail": f"newest line is {hrs:.1f}h old (>{LINES_STALE_HOURS}h) — scrape may be broken"})
        else:
            findings.append({"level": "ok", "name": "prop_lines_fresh",
                             "detail": f"newest line {hrs:.1f}h old"})

        daily = c.execute(text("""
            SELECT (snapshot_at AT TIME ZONE 'America/Los_Angeles')::date AS d,
                   COUNT(DISTINCT (player_id, stat_type)) AS n
            FROM prop_lines
            WHERE snapshot_at > NOW() - INTERVAL '8 days'
            GROUP BY 1 ORDER BY 1
        """)).all()
        by_day = {str(r[0]): int(r[1]) for r in daily}
        today = _scalar(c, "SELECT (NOW() AT TIME ZONE 'America/Los_Angeles')::date::text")
        today_n = by_day.get(today, 0)
        prior = [n for d, n in by_day.items() if d != today]
        avg_prior = sum(prior) / len(prior) if prior else 0
        if avg_prior >= LINES_MIN_BASELINE and today_n < LINES_THIN_FRAC * avg_prior:
            findings.append({"level": "warn", "name": "slate_thin",
                             "detail": f"today {today_n} distinct lines vs {avg_prior:.0f} avg — thin/missing slate"})
        else:
            findings.append({"level": "ok", "name": "slate_volume",
                             "detail": f"today {today_n} lines (7d avg {avg_prior:.0f})"})

        # ── box scores: recent FINAL games must have player_games ────────────
        missing = c.execute(text("""
            SELECT g.sport_code, COUNT(*) AS n
            FROM games g
            WHERE g.status = 'final'
              AND g.game_date BETWEEN
                    (NOW() AT TIME ZONE 'America/Los_Angeles')::date - INTERVAL '2 days'
                AND (NOW() AT TIME ZONE 'America/Los_Angeles')::date - INTERVAL '1 day'
              AND NOT EXISTS (SELECT 1 FROM player_games pg WHERE pg.game_id = g.game_id)
            GROUP BY g.sport_code ORDER BY 2 DESC
        """)).all()
        if missing:
            detail = ", ".join(f"{r[0]}:{r[1]}" for r in missing)
            findings.append({"level": "warn", "name": "boxscores_missing",
                             "detail": f"final games w/o box scores (settling stalls) — {detail}"})
        else:
            findings.append({"level": "ok", "name": "boxscores",
                             "detail": "recent final games all have box scores"})

        # ── injuries: feed not cold ──────────────────────────────────────────
        ihrs = _scalar(c, "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(fetched_at)))/3600 "
                          "FROM player_injuries")
        if ihrs is None or ihrs > INJURY_STALE_HOURS:
            findings.append({"level": "warn", "name": "injuries_stale",
                             "detail": f"newest injury report {('n/a' if ihrs is None else f'{ihrs:.1f}h')} old (>{INJURY_STALE_HOURS}h)"})
        else:
            findings.append({"level": "ok", "name": "injuries_fresh",
                             "detail": f"newest injury report {ihrs:.1f}h old"})

    return findings


def _alert(findings: list[dict]):
    warns = [f for f in findings if f["level"] == "warn"]
    if not warns or not settings.discord_webhook_url:
        return
    import requests
    lines = "\n".join(f"• **{f['name']}** — {f['detail']}" for f in warns)
    payload = {"embeds": [{
        "title": "⚠️ prop-edge ingest monitor",
        "description": f"{len(warns)} ingest anomaly(ies):\n{lines}",
        "color": 0xE8A317,
        "footer": {"text": "ingest_monitor"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("ingest_monitor_alert_failed", error=str(e)[:120])


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord alert")
    args = ap.parse_args()

    findings = run_checks()
    for f in findings:
        mark = "⚠️ " if f["level"] == "warn" else "✓ "
        print(f"  {mark}{f['name']}: {f['detail']}")
    warns = [f for f in findings if f["level"] == "warn"]
    log.info("ingest_monitor", checks=len(findings), warnings=len(warns))
    if not args.quiet:
        _alert(findings)
    return warns


if __name__ == "__main__":
    main()
