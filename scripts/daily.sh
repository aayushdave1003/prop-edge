#!/usr/bin/env bash
# Daily ritual for prop-edge. Run every morning.
# Self-healing: re-fetches yesterday + today, auto-resolves placeholder game_ids,
# rebuilds rolling features after new box scores land, settles before AND after
# logging new picks so anything for already-final games settles in the same run.
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

echo "--- Rebuild NBA rolling features ---"
python -m props.features.nba_rolling
python -m props.features.nba_opposing_team
python -m props.features.nba_home_away
python -m props.features.nba_back_to_back
python -m props.features.nba_streak

echo "--- Rebuild MLB rolling features ---"
python -m props.features.mlb_rolling
python -m props.features.mlb_opposing_pitcher
python -m props.features.mlb_opposing_lineup
python -m props.features.mlb_batter_vs_pitcher

echo "--- Injuries ---"
python -m props.ingest.injuries

echo "--- Settle yesterday's picks (auto-resolves placeholders) ---"
python -m props.picks.settle_picks

echo "--- Generate + log today's picks ---"
python -m props.picks.log_picks

echo "--- Second settle pass: catch tonight's picks for already-final games ---"
python -m props.picks.settle_picks

echo "=== Done. Check dashboard: .venv/bin/streamlit run ui/dashboard.py ==="
