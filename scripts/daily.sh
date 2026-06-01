#!/usr/bin/env bash
# prop-edge daily ritual — runs every morning via cron (see scripts/install_cron.sh)
# Self-healing: re-fetches yesterday + today, auto-resolves placeholder game_ids,
# rebuilds rolling features after new box scores land, settles before AND after
# logging new picks so anything for already-final games settles in the same run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# Activate venv
source .venv/bin/activate

# Log everything to a dated file
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/daily_$(date +%Y-%m-%d).log"

exec > >(tee -a "$LOGFILE") 2>&1

YESTERDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "yesterday" +%Y-%m-%d)
TODAY=$(date +%Y-%m-%d)
TOMORROW=$(date -v+1d +%Y-%m-%d 2>/dev/null || date -d "tomorrow" +%Y-%m-%d)

echo ""
echo "======================================================"
echo "  prop-edge daily ritual: $TODAY"
echo "  Log: $LOGFILE"
echo "======================================================"

# ── 1. Schedules (yesterday + today + tomorrow for early-morning runs) ──────
echo "--- MLB schedule ---"
python -m props.ingest.mlb_schedule "$YESTERDAY"
python -m props.ingest.mlb_schedule "$TODAY"
python -m props.ingest.mlb_schedule "$TOMORROW"

echo "--- NBA schedule ---"
python -m props.ingest.nba_schedule "$YESTERDAY"
python -m props.ingest.nba_schedule "$TODAY"
python -m props.ingest.nba_schedule "$TOMORROW"

echo "--- WNBA schedule ---"
python -m props.ingest.wnba_schedule "$YESTERDAY"
python -m props.ingest.wnba_schedule "$TODAY"
python -m props.ingest.wnba_schedule "$TOMORROW"

echo "--- NHL schedule ---"
python -m props.ingest.nhl_schedule "$YESTERDAY"
python -m props.ingest.nhl_schedule "$TODAY"
python -m props.ingest.nhl_schedule "$TOMORROW"

# ── 2. Box scores ────────────────────────────────────────────────────────────
echo "--- Box scores ---"
python -m props.ingest.mlb_boxscores
python -m props.ingest.nba_boxscores
python -m props.ingest.wnba_boxscores
python -m props.ingest.nhl_boxscores

# ── 3. Rolling features ──────────────────────────────────────────────────────
echo "--- NBA rolling features ---"
python -m props.features.nba_rolling
python -m props.features.nba_opposing_team
python -m props.features.nba_home_away
python -m props.features.nba_back_to_back
python -m props.features.nba_streak

echo "--- WNBA rolling features ---"
python -m props.features.wnba_rolling

echo "--- NHL rolling features ---"
python -m props.features.nhl_rolling

echo "--- MLB rolling features ---"
python -m props.features.mlb_rolling
python -m props.features.mlb_opposing_pitcher
python -m props.features.mlb_opposing_lineup
python -m props.features.mlb_batter_vs_pitcher

# ── 4. Live data refreshes ───────────────────────────────────────────────────
echo "--- Injuries ---"
python -m props.ingest.injuries

echo "--- PrizePicks lines ---"
python -m props.ingest.prizepicks --sport nba  || true
python -m props.ingest.prizepicks --sport mlb  || true

# ── 5. Settle previous picks ─────────────────────────────────────────────────
echo "--- Settle yesterday's picks ---"
python -m props.picks.settle_picks

# ── 6. Generate + log today's picks ─────────────────────────────────────────
echo "--- Generate and log today's picks ---"
python -m props.picks.predict_today --date "$TODAY"
python -m props.picks.log_picks

echo "--- Confirm MLB starters (morning check) ---"
python -m props.picks.confirm_starters --date "$TODAY" || true

# ── 7. Second settle pass ────────────────────────────────────────────────────
echo "--- Second settle pass ---"
python -m props.picks.settle_picks

# ── 8. Weekly backtest (Mondays) ─────────────────────────────────────────────
if [ "$(date +%u)" = "1" ]; then
    echo "--- Weekly backtest (Monday) ---"
    SINCE_90=$(date -v-90d +%Y-%m-%d 2>/dev/null || date -d '90 days ago' +%Y-%m-%d)
    python -m props.picks.backtest --sport nba --since "$SINCE_90" || true
    python -m props.picks.backtest --sport mlb --since "$SINCE_90" || true
fi

# ── 9. Rotate old logs (keep 30 days) ────────────────────────────────────────
find "$LOG_DIR" -name "daily_*.log" -mtime +30 -delete 2>/dev/null || true

echo ""
echo "======================================================"
echo "  Done: $(date)"
echo "  Dashboard: streamlit run ui/dashboard.py"
echo "======================================================"
