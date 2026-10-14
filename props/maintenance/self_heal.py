"""Cloud self-heal — runs at the end of the daily pipeline so the system fixes
itself on GitHub Actions, with no Mac and no human in the loop.

The pipeline already settles nightly, but a single transient step failure (a
box-score fetch timing out, a status not flipping) can leave picks stuck
unsettled until someone notices. This catches that automatically: if any picks
are stranded on games that are already final/past, it re-attempts box scores
(which also flip stale statuses) and re-settles. The settle path itself voids
truly-unrecoverable orphans, so this converges to zero.

Posts a short Discord note when it heals something — AND, just as importantly,
when it CANNOT. A backlog the recovery chain fails to clear is the signal that
something is structurally broken rather than transient, and that was the one
case this stayed silent for: `if healed > 0` meant a permanent backlog produced
no alert at all. 7,079 `hits_runs_rbis` picks sat unsettleable for months while
this ran the full recovery every night, healed nothing, and said nothing. (The
cause was a settlement bug — the box score has no key by that name — but the
monitoring gap is what let it run that long.)

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

# A pick stuck on a game this old is not waiting for anything — box scores have
# long landed and the settle path voids true orphans after 2 days. If the
# recovery chain cannot clear it, it is broken, not slow.
STUCK_STALE_DAYS = 3

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


def _stale_breakdown() -> list[tuple[str, str, int, str]]:
    """(sport, stat_type, n, oldest_game_date) for picks stuck on games older
    than STUCK_STALE_DAYS — named so the alert points at the cause rather than
    just reporting a number."""
    with session_scope() as s:
        return [(r[0], r[1], int(r[2]), str(r[3])) for r in s.execute(text(f"""
            SELECT pk.sport_code, pk.stat_type, COUNT(*) n, MIN(g.game_date) oldest
            FROM picks pk JOIN games g USING (game_id)
            WHERE pk.leg_result IS NULL
              AND g.game_date < (NOW() AT TIME ZONE 'America/Los_Angeles')::date
                                - INTERVAL '{STUCK_STALE_DAYS} days'
            GROUP BY 1, 2 ORDER BY 3 DESC
        """))]


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

    msg = None
    if healed > 0:
        msg = (f"🔧 prop-edge self-heal: auto-cleared {healed} stuck pick(s) "
               f"({before}→{after}).")

    # The case this used to miss entirely: recovery ran and nothing moved.
    stale = _stale_breakdown()
    if stale:
        total = sum(n for _, _, n, _ in stale)
        worst = ", ".join(f"{sp}|{st} {n} (since {d})" for sp, st, n, d in stale[:3])
        more = f" +{len(stale) - 3} more" if len(stale) > 3 else ""
        warn = (f"⚠️ prop-edge self-heal: {total} pick(s) stuck on games >"
                f"{STUCK_STALE_DAYS}d old and recovery did not clear them — {worst}{more}. "
                f"Unsettled picks are invisible to the win rate, the ROI, and the "
                f"auto-tuned cutoffs, so this skews every reported number.")
        msg = f"{msg}\n{warn}" if msg else warn
        log.warning("self_heal_stuck_stale", total=total,
                    worst=[f"{sp}|{st}:{n}" for sp, st, n, _ in stale[:5]])

    if msg and settings.discord_webhook_url:
        try:
            requests.post(settings.discord_webhook_url, json={"content": msg}, timeout=10)
        except Exception as e:
            log.warning("self_heal_alert_failed", error=str(e)[:120])
    return healed


if __name__ == "__main__":
    print(f"self-heal cleared {run()} stuck picks")
