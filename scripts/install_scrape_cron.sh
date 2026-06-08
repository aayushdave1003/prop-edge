#!/usr/bin/env bash
# Install the PrizePicks line-scrape cron on this Mac (idempotent).
#
# Runs scrape_lines.sh at ~6:40a / 10:03a / 4:07p / 7:13p Pacific. The 6:40a run
# lands fresh lines before the GitHub Actions daily pipeline generates picks at
# 7a PT. Times are LOCAL (Mac tz) — no UTC/DST math needed, unlike the GHA crons.
set -euo pipefail

SCRIPT="/Users/aayushdave/props/scripts/scrape_lines.sh"
chmod +x "$SCRIPT"

# Desired entries, tagged so we can replace cleanly on re-run.
TAG="# prop-edge scrape_lines"
NEW=$(cat <<EOF
40 6 * * * $SCRIPT $TAG
3 10 * * * $SCRIPT $TAG
7 16 * * * $SCRIPT $TAG
13 19 * * * $SCRIPT $TAG
EOF
)

# Keep any non-prop-edge crontab lines, drop our old tagged ones, add fresh.
EXISTING=$(crontab -l 2>/dev/null | grep -v "$TAG" || true)
printf '%s\n%s\n' "$EXISTING" "$NEW" | grep -v '^$' | crontab -

echo "Installed. Current crontab:"
crontab -l
