#!/usr/bin/env bash
# Scrape PrizePicks lines to the Railway (prod) DB from a residential IP.
#
# Why this runs on the Mac and not GitHub Actions: PrizePicks blocks datacenter
# IPs (Cloudflare), so the GHA scrape returns HTTPError and lines go stale.
# Everything else in the pipeline stays on GHA — this cron only owns the one
# step that can't run from the cloud. If the Mac is asleep at a scrape time,
# props/maintenance/ingest_monitor (in the GHA daily run) will flag stale lines.
#
# Install: scripts/install_scrape_cron.sh   (runs ~6:40a / 10a / 4p / 7p PT)
set -uo pipefail

REPO="/Users/aayushdave/props"
cd "$REPO" || exit 1

# Load creds (cron has no env) and target the Railway DB.
if [ -f ".env" ]; then set -a; source .env; set +a; fi
export DATABASE_URL="${RAILWAY_DATABASE_URL:-${DATABASE_URL:-}}"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

mkdir -p logs
LOG="logs/scrape_lines_$(date +%Y-%m-%d).log"

{
    echo "=================================================="
    echo "scrape_lines: $(date)  ->  Railway DB"
    # One call scrapes ALL sports — the projections endpoint returns every league
    # in one payload, so per-sport invocations just repeat the same full scrape.
    "$PY" -m props.ingest.prizepicks || echo "WARN: scrape failed"
    echo "done: $(date)"
} >> "$LOG" 2>&1

# Keep 30 days of logs.
find logs -name "scrape_lines_*.log" -mtime +30 -delete 2>/dev/null || true
