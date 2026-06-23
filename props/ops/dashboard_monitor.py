"""Dashboard perf / uptime monitor — a synthetic check beyond /_stcore/health.

Streamlit's /_stcore/health only says the server process is up; it says nothing
about whether a real page actually renders or how slow it is (a cold Railway
container or a slow DB query can make the app technically "healthy" but unusable).
So this hits BOTH the health endpoint and a real view, times them, and pings
Discord when the app is down or render latency blows past a threshold.

Cold starts on Railway's free-ish tier are genuinely slow, so the latency gate is
deliberately generous — it's there to catch a wedged app, not to chase ms.

Run:  python -m props.ops.dashboard_monitor          (alerts on problems)
      python -m props.ops.dashboard_monitor --quiet  (print only, no Discord)
"""
from __future__ import annotations

import argparse
import time

from props.utils.config import settings
from props.utils.logging import log, configure_logging

HEALTH_TIMEOUT = 15        # seconds; health should answer fast once warm
RENDER_TIMEOUT = 30        # a real page, tolerant of a cold-start spin-up
SLOW_RENDER_S = 12.0       # warn if a warm render is slower than this


def _timed_get(url: str, timeout: int) -> tuple[int | None, float, str]:
    import requests
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code, time.monotonic() - t0, ""
    except Exception as e:
        return None, time.monotonic() - t0, str(e)[:80]


def run_checks() -> list[dict]:
    base = settings.dashboard_url.rstrip("/")
    findings: list[dict] = []

    code, dt, err = _timed_get(f"{base}/_stcore/health", HEALTH_TIMEOUT)
    if code == 200:
        findings.append({"level": "ok", "name": "health",
                         "detail": f"200 in {dt*1000:.0f}ms"})
    else:
        findings.append({"level": "warn", "name": "health_down",
                         "detail": f"health {code or 'ERR'} after {dt:.1f}s"
                                   + (f" ({err})" if err else "")})

    # a real render — the results page is read-only and self-contained
    code, dt, err = _timed_get(f"{base}/?view=results", RENDER_TIMEOUT)
    if code != 200:
        findings.append({"level": "warn", "name": "render_down",
                         "detail": f"GET ?view=results {code or 'ERR'} after {dt:.1f}s"
                                   + (f" ({err})" if err else "")})
    elif dt > SLOW_RENDER_S:
        findings.append({"level": "warn", "name": "render_slow",
                         "detail": f"render took {dt:.1f}s (>{SLOW_RENDER_S:.0f}s)"})
    else:
        findings.append({"level": "ok", "name": "render",
                         "detail": f"200 in {dt:.1f}s"})
    return findings


def _alert(findings: list[dict]):
    warns = [f for f in findings if f["level"] == "warn"]
    if not warns or not settings.discord_webhook_url:
        return
    import requests
    lines = "\n".join(f"• **{f['name']}** — {f['detail']}" for f in warns)
    payload = {"embeds": [{
        "title": "⚠️ prop-edge dashboard monitor",
        "description": f"{settings.dashboard_url}\n{lines}",
        "color": 0xE74C3C,
        "footer": {"text": "dashboard_monitor"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)  # type: ignore[arg-type]
    except Exception as e:
        log.warning("dashboard_monitor_alert_failed", error=str(e)[:120])


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord")
    args = ap.parse_args()
    findings = run_checks()
    for f in findings:
        print(f"  {'⚠️ ' if f['level'] == 'warn' else '✓ '}{f['name']}: {f['detail']}")
    warns = [f for f in findings if f["level"] == "warn"]
    log.info("dashboard_monitor", checks=len(findings), warnings=len(warns))
    if not args.quiet:
        _alert(findings)
    return warns


if __name__ == "__main__":
    main()
