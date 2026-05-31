#!/usr/bin/env bash
# Install prop-edge cron jobs. Run once: bash scripts/install_cron.sh
# Jobs installed:
#   07:00 AM daily  — full daily ritual (box scores + features + picks)
#   10:00 AM daily  — refresh PrizePicks lines mid-morning
#   04:00 PM daily  — refresh PrizePicks + injuries + confirm MLB starters
#   07:00 PM daily  — final line/injury refresh + confirm MLB starters again
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/.venv/bin/python"
LOGDIR="$REPO/logs"
mkdir -p "$LOGDIR"

# Build cron lines
DAILY="0 7 * * * cd $REPO && bash scripts/daily.sh >> $LOGDIR/cron.log 2>&1"
REFRESH_AM="0 10 * * * cd $REPO && source .venv/bin/activate && python -m props.ingest.prizepicks --sport nba >> $LOGDIR/cron.log 2>&1 && python -m props.ingest.prizepicks --sport mlb >> $LOGDIR/cron.log 2>&1"
REFRESH_PM="0 16 * * * cd $REPO && source .venv/bin/activate && python -m props.ingest.prizepicks --sport nba >> $LOGDIR/cron.log 2>&1 && python -m props.ingest.prizepicks --sport mlb >> $LOGDIR/cron.log 2>&1 && python -m props.ingest.injuries >> $LOGDIR/cron.log 2>&1 && python -m props.picks.confirm_starters >> $LOGDIR/cron.log 2>&1"
REFRESH_EVE="0 19 * * * cd $REPO && source .venv/bin/activate && python -m props.ingest.prizepicks --sport nba >> $LOGDIR/cron.log 2>&1 && python -m props.ingest.injuries >> $LOGDIR/cron.log 2>&1 && python -m props.picks.confirm_starters >> $LOGDIR/cron.log 2>&1"

# Remove old prop-edge entries, add new ones
TMPFILE=$(mktemp)
crontab -l 2>/dev/null | grep -v "prop-edge\|props/scripts\|props/ingest\|daily.sh" > "$TMPFILE" || true

echo "# prop-edge: daily ritual" >> "$TMPFILE"
echo "$DAILY" >> "$TMPFILE"
echo "# prop-edge: mid-morning line refresh" >> "$TMPFILE"
echo "$REFRESH_AM" >> "$TMPFILE"
echo "# prop-edge: afternoon line + injury refresh" >> "$TMPFILE"
echo "$REFRESH_PM" >> "$TMPFILE"
echo "# prop-edge: evening pre-game refresh" >> "$TMPFILE"
echo "$REFRESH_EVE" >> "$TMPFILE"

crontab "$TMPFILE"
rm "$TMPFILE"

echo "Cron jobs installed:"
crontab -l | grep -A1 "prop-edge"
echo ""
echo "Logs will write to: $LOGDIR/cron.log"
echo "Run 'crontab -l' to verify."
