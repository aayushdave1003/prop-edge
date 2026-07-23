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

from props.utils.db import engine, db_banner
from props.utils.config import settings
from props.utils.logging import log, configure_logging

LINES_STALE_HOURS = 18      # refresh.yml scrapes ~3x/day; >18h = scrape broken
LINES_THIN_FRAC = 0.30      # today's lines-PER-GAME < 30% of the 7-day avg = thin coverage
LINES_MIN_BASELINE = 50     # only flag "thin" once there's a real baseline
LINES_COLLAPSE_FRAC = 0.10  # today's TOTAL lines < 10% of avg = feed likely broken (flag even if per-game ok)
INJURY_STALE_HOURS = 40     # injuries refresh daily; >40h = feed cold
ODDS_QUOTA_LOW = 1500       # warn while there's still runway to top up


def _scalar(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def _odds_quota_finding() -> dict | None:
    """Read The Odds API remaining-requests via the FREE /sports endpoint (costs
    0 requests) and warn before the quota exhausts — so the market_edge feed
    never silently dies mid-month again. Skipped when no key is configured."""
    key = getattr(settings, "odds_api_key", "") or ""
    if not key:
        return None
    import requests
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/",
                         params={"apiKey": key}, timeout=10)
    except Exception as e:
        return {"level": "warn", "name": "odds_api_unreachable",
                "detail": f"could not reach The Odds API ({str(e)[:60]})"}
    if r.status_code == 401:
        return {"level": "warn", "name": "odds_api_key_invalid",
                "detail": "401 — Odds API key rejected (expired/revoked)"}
    remaining = r.headers.get("x-requests-remaining")
    used = r.headers.get("x-requests-used")
    if remaining is None:
        return {"level": "ok", "name": "odds_api",
                "detail": f"reachable (HTTP {r.status_code}), no quota header"}
    rem = int(float(remaining))
    if rem <= 0:
        return {"level": "warn", "name": "odds_quota_exhausted",
                "detail": f"0 requests left (used {used}) — market_edge is off until "
                          "the plan is topped up at the-odds-api.com"}
    if rem < ODDS_QUOTA_LOW:
        return {"level": "warn", "name": "odds_quota_low",
                "detail": f"{rem} requests left (used {used}) — top up soon to keep market_edge live"}
    return {"level": "ok", "name": "odds_quota",
            "detail": f"{rem} requests remaining (used {used})"}


def run_checks() -> list[dict]:
    """Return a list of findings: {level: 'ok'|'warn', name, detail}."""
    findings: list[dict] = []
    with engine.connect() as c:
        # ── prop_lines: freshness + slate volume ─────────────────────────────
        # When LINES_PAUSED, an empty/stale slate is EXPECTED — suppress the
        # freshness + volume warnings so they don't false-alarm. (The flag is
        # explicit on purpose: the monitor stays sharp for every OTHER failure,
        # and a future real scrape break would still surface once resumed.)
        if settings.lines_paused:
            findings.append({"level": "ok", "name": "lines_paused",
                             "detail": "LINES_PAUSED — no new lines expected (scrape source blocked); "
                                       "freshness/slate checks suppressed"})
        else:
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
                       COUNT(DISTINCT (player_id, stat_type)) AS n,
                       COUNT(DISTINCT game_id) AS games
                FROM prop_lines
                WHERE snapshot_at > NOW() - INTERVAL '8 days'
                GROUP BY 1 ORDER BY 1
            """)).all()
            by_day = {str(r[0]): (int(r[1]), int(r[2])) for r in daily}
            today = _scalar(c, "SELECT (NOW() AT TIME ZONE 'America/Los_Angeles')::date::text")
            today_n, today_g = by_day.get(today, (0, 0))
            prior = [(n, g) for d, (n, g) in by_day.items() if d != today]
            avg_prior_n = sum(n for n, _ in prior) / len(prior) if prior else 0
            # Normalize by game count: a LIGHT game day (few games, no WNBA) has
            # fewer total lines but NORMAL lines-per-game — that's the schedule,
            # not a bug, so it shouldn't alarm. Flag only a real per-game coverage
            # gap, or a near-total collapse (broken feed).
            today_lpg = today_n / today_g if today_g else 0.0
            prior_lpg = [n / g for n, g in prior if g]
            avg_prior_lpg = sum(prior_lpg) / len(prior_lpg) if prior_lpg else 0.0
            thin_per_game = avg_prior_lpg > 0 and today_lpg < LINES_THIN_FRAC * avg_prior_lpg
            collapse = avg_prior_n >= LINES_MIN_BASELINE and today_n < LINES_COLLAPSE_FRAC * avg_prior_n
            if avg_prior_n >= LINES_MIN_BASELINE and (thin_per_game or collapse):
                why = "feed likely broken" if collapse else "thin per-game coverage"
                findings.append({"level": "warn", "name": "slate_thin",
                                 "detail": f"today {today_n} lines / {today_g} games = {today_lpg:.0f}/game "
                                           f"vs {avg_prior_lpg:.0f}/game avg — {why}"})
            else:
                findings.append({"level": "ok", "name": "slate_volume",
                                 "detail": f"today {today_n} lines / {today_g} games "
                                           f"({today_lpg:.0f}/game; 7d avg {avg_prior_lpg:.0f}/game)"})

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

    # ── odds API quota (drives market_edge) ──────────────────────────────────
    odds = _odds_quota_finding()
    if odds is not None:
        findings.append(odds)

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
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)  # type: ignore[arg-type]
    except Exception as e:
        log.warning("ingest_monitor_alert_failed", error=str(e)[:120])


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord alert")
    args = ap.parse_args()

    print(db_banner())
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
