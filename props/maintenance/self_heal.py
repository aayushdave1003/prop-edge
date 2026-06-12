"""Cloud self-heal — runs at the end of the daily pipeline so the system fixes
itself on GitHub Actions, with no Mac and no human in the loop.

The pipeline already settles nightly, but a single transient step failure (a
box-score fetch timing out, a status not flipping) can leave picks stuck
unsettled until someone notices. This catches that automatically: if any picks
are stranded on games that are already final/past, it re-attempts box scores
(which also flip stale statuses) and re-settles. The settle path itself voids
truly-unrecoverable orphans, so this converges to zero.

Posts a short Discord note ONLY when it actually heals something, so you know it
self-corrected without having to check.

Wired into daily.sh (step 7d). Run standalone: python -m props.maintenance.self_heal
"""
import subprocess
import sys

import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.config import settings
from props.utils.logging import log, configure_logging

# Box-score + settle steps to re-attempt when picks are stuck. Subprocess so a
# failure in one is isolated and never aborts the heal.
RECOVERY = [
    ["props.ingest.nba_boxscores"],
    ["props.ingest.mlb_boxscores", "--since-days", "5"],
    ["props.ingest.wnba_boxscores"],
    ["props.ingest.nhl_boxscores"],
    ["props.picks.settle_picks"],
]

STUCK_SQL = """
    SELECT COUNT(*) FROM picks pk JOIN games g USING (game_id)
    WHERE pk.leg_result IS NULL
      AND (g.status = 'final'
           OR g.game_date < (NOW() AT TIME ZONE 'America/Los_Angeles')::date
                            - INTERVAL '1 day')
"""


def _stuck_count() -> int:
    with session_scope() as s:
        return int(s.execute(text(STUCK_SQL)).scalar() or 0)


def run() -> int:
    """Heal stuck picks. Returns the number cleared."""
    configure_logging()
    before = _stuck_count()
    if before == 0:
        log.info("self_heal_clean", stuck=0)
        return 0

    log.info("self_heal_recovering", stuck=before)
    for cmd in RECOVERY:
        try:
            subprocess.run([sys.executable, "-m", *cmd], check=False, timeout=1200)
        except Exception as e:  # pragma: no cover - subprocess env-specific
            log.warning("self_heal_step_failed", cmd=cmd[0], error=str(e)[:120])

    after = _stuck_count()
    healed = before - after
    log.info("self_heal_done", before=before, after=after, healed=healed)
    if healed > 0 and settings.discord_webhook_url:
        try:
            requests.post(settings.discord_webhook_url, json={"content":
                f"🔧 prop-edge self-heal: auto-cleared {healed} stuck pick(s) "
                f"({before}→{after})."}, timeout=10)
        except Exception as e:
            log.warning("self_heal_alert_failed", error=str(e)[:120])
    return healed


if __name__ == "__main__":
    print(f"self-heal cleared {run()} stuck picks")
