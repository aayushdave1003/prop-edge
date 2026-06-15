"""Consolidated daily digest — ONE Discord message instead of four separate
monitor pings.

The silent-outage lesson: the ingest/0-pick alerts actually fired the whole time
but went unseen amid the per-monitor noise. This runs every monitor's checks
(ingest, data audit, feature drift, dashboard health) and sends a single grouped
summary once a day, so a real warning stands out instead of drowning. The
individual monitors stay runnable on demand; daily.sh uses this for the alert.

Run:  python -m props.ops.digest          (prints all + sends one digest)
      python -m props.ops.digest --quiet  (print only, no Discord)
"""
from __future__ import annotations

import argparse

from props.maintenance.ingest_monitor import run_checks as _ingest
from props.ops.data_audit import run_checks as _audit
from props.ops.feature_drift import run_checks as _drift
from props.ops.dashboard_monitor import run_checks as _dash
from props.utils.config import settings
from props.utils.db import db_banner
from props.utils.logging import log, configure_logging

SECTIONS = [("Ingest", _ingest), ("Data audit", _audit),
            ("Feature drift", _drift), ("Dashboard", _dash)]


def gather() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name, fn in SECTIONS:
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = [{"level": "warn", "name": f"{name.lower()}_failed",
                          "detail": str(e)[:120]}]
    return out


def _send(sections: dict[str, list[dict]]) -> int:
    warns = sum(1 for fs in sections.values() for f in fs if f["level"] == "warn")
    lines = []
    for name, fs in sections.items():
        w = [f for f in fs if f["level"] == "warn"]
        if w:
            lines.append(f"**{name}** ⚠️")
            lines += [f"• {f['name']} — {f['detail']}" for f in w]
        else:
            lines.append(f"**{name}** ✓ ({len(fs)} ok)")
    if not settings.discord_webhook_url:
        return warns
    import requests
    payload = {"embeds": [{
        "title": (f"🗞️ prop-edge daily digest — {warns} issue(s)" if warns
                  else "🗞️ prop-edge daily digest — all clear"),
        "description": "\n".join(lines)[:4000],
        "color": 0xE74C3C if warns else 0x2ECC71,
        "footer": {"text": "daily digest"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=12)
    except Exception as e:
        log.warning("digest_send_failed", error=str(e)[:120])
    return warns


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord")
    args = ap.parse_args()
    print(db_banner())
    sections = gather()
    for name, fs in sections.items():
        for f in fs:
            print(f"  [{name}] {'⚠️ ' if f['level'] == 'warn' else '✓ '}"
                  f"{f['name']}: {f['detail']}")
    warns = sum(1 for fs in sections.values() for f in fs if f["level"] == "warn")
    log.info("daily_digest", sections=len(sections), warnings=warns)
    if not args.quiet:
        _send(sections)
    return warns


if __name__ == "__main__":
    main()
