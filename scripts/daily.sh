#!/usr/bin/env bash
# Daily ritual for prop-edge. Run every morning.
# Self-healing: re-fetches yesterday + today, auto-resolves placeholder game_ids.
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

YESTERDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "yesterday" +%Y-%m-%d)
TODAY=$(date +%Y-%m-%d)

echo "=== prop-edge daily ritual: $TODAY (also re-fetching $YESTERDAY) ==="

echo "--- MLB schedule (yesterday + today) ---"
python -m props.ingest.mlb_schedule "$YESTERDAY"
python -m props.ingest.mlb_schedule "$TODAY"

echo "--- NBA schedule (yesterday + today) ---"
python -m props.ingest.nba_schedule "$YESTERDAY"
python -m props.ingest.nba_schedule "$TODAY"

echo "--- Box scores ---"
python -m props.ingest.mlb_boxscores
python -m props.ingest.nba_boxscores

echo "--- Injuries ---"
python -m props.ingest.injuries

echo "--- Settle yesterday's picks (auto-resolves placeholders) ---"
python -m props.picks.settle_picks

echo "--- Generate + log today's picks ---"
python -m props.picks.log_picks

echo "=== Done. Check dashboard: .venv/bin/streamlit run ui/dashboard.py ==="
