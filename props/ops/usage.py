"""Cost / usage snapshot — Odds API credits, scrape volume, pipeline freshness,
and DB growth in one place, so a blow-up (quota draining, scrape stalled, DB
ballooning) is visible before it bites.

`gather()` returns a plain dict the dashboard renders (Ops view) and the CLI
prints. Railway dollar spend isn't exposed by a simple API (it needs the Railway
dashboard / a project token), so we track the things we CAN measure — DB size +
row growth as the cost proxy — and link out for the bill.

Run:  python -m props.ops.usage          (print a snapshot)
      python -m props.ops.usage --json    (machine-readable)
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from props.utils.db import engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging

RAILWAY_BILLING_URL = "https://railway.app/account/usage"


def _odds() -> dict:
    """Reuse the monitor's free quota read (costs 0 Odds API requests)."""
    try:
        from props.maintenance.ingest_monitor import _odds_quota_finding
        f = _odds_quota_finding()
    except Exception as e:
        return {"detail": f"quota check failed ({str(e)[:60]})", "level": "warn"}
    if f is None:
        return {"detail": "no Odds API key configured", "level": "ok"}
    return {"detail": f["detail"], "level": f["level"]}


def gather() -> dict:
    out: dict = {"odds": _odds(), "railway_billing_url": RAILWAY_BILLING_URL}
    with engine.connect() as c:
        # ── scrape volume (prop_lines) ───────────────────────────────────────
        rows = c.execute(text("""
            SELECT (snapshot_at AT TIME ZONE 'America/Los_Angeles')::date AS d,
                   COUNT(DISTINCT (player_id, stat_type)) AS n
            FROM prop_lines
            WHERE snapshot_at > NOW() - INTERVAL '10 days'
            GROUP BY 1 ORDER BY 1 DESC
        """)).all()
        by_day = [(str(r[0]), int(r[1])) for r in rows]
        last_scrape_h = c.execute(text(
            "SELECT EXTRACT(EPOCH FROM (NOW()-MAX(snapshot_at)))/3600 FROM prop_lines"
        )).scalar()
        today_pt = str(c.execute(text(
            "SELECT (NOW() AT TIME ZONE 'America/Los_Angeles')::date::text")).scalar() or "")
        out["scrape"] = {
            "by_day": by_day,
            "last_scrape_hours": round(float(last_scrape_h), 1) if last_scrape_h else None,
            # actual today (PT) — 0 when the scrape is stale, NOT the most-recent day
            "today_lines": dict(by_day).get(today_pt, 0),
            "latest_day": by_day[0] if by_day else None,   # (date, n) of newest data
            "avg_10d": round(sum(n for _, n in by_day) / len(by_day), 0) if by_day else 0,
        }

        # ── pipeline freshness (picks) ───────────────────────────────────────
        out["picks"] = {
            "last_picked_hours": _hrs(c, "SELECT MAX(picked_at) FROM picks"),
            "today_n": c.execute(text("""
                SELECT COUNT(*) FROM picks
                WHERE (picked_at AT TIME ZONE 'America/Los_Angeles')::date
                    = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
            """)).scalar() or 0,
            "settled_7d": c.execute(text("""
                SELECT COUNT(*) FROM picks
                WHERE leg_result IN ('win','loss','push')
                  AND picked_at > NOW() - INTERVAL '7 days'
            """)).scalar() or 0,
        }

        # ── DB size + biggest tables (the cost proxy we can measure) ─────────
        out["db"] = {
            "size": c.execute(text(
                "SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar(),
            "tables": [(r[0], r[1]) for r in c.execute(text("""
                SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid))
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND n.nspname = 'public'
                ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 6
            """)).all()],
        }
    return out


def _hrs(conn, sql: str):
    ts = conn.execute(text(sql)).scalar()
    if ts is None:
        return None
    h = conn.execute(text("SELECT EXTRACT(EPOCH FROM (NOW()-:t))/3600"),
                     {"t": ts}).scalar()
    return round(float(h), 1)


def _print(m: dict):
    print("=== prop-edge cost / usage ===\n")
    print(f"Odds API   {m['odds']['detail']}")
    s = m["scrape"]
    latest = f" (newest day {s['latest_day'][0]}: {s['latest_day'][1]})" if s.get("latest_day") else ""
    stale = "  ⚠️ STALE" if (s["last_scrape_hours"] or 0) > 18 else ""
    print(f"Scrape     today {s['today_lines']} distinct lines{latest} "
          f"(10d avg {s['avg_10d']:.0f}); last scrape {s['last_scrape_hours']}h ago{stale}")
    p = m["picks"]
    print(f"Pipeline   {p['today_n']} picks today; last slate {p['last_picked_hours']}h ago; "
          f"{p['settled_7d']} settled in 7d")
    print(f"\nDatabase   {m['db']['size']} total")
    for name, size in m["db"]["tables"]:
        print(f"             {size:>9}  {name}")
    print(f"\nRailway $  see {m['railway_billing_url']} (no metered API; DB size above is the proxy)")


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    m = gather()
    if args.json:
        print(json.dumps(m, indent=2, default=str))
    else:
        _print(m)
    log.info("usage_snapshot", today_lines=m["scrape"]["today_lines"],
             db_size=m["db"]["size"], picks_today=m["picks"]["today_n"])


if __name__ == "__main__":
    main()
