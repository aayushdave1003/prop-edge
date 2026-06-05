#!/usr/bin/env bash
# prop-edge daily ritual — runs every morning via cron (see scripts/install_cron.sh)
# Self-healing: re-fetches yesterday + today, auto-resolves placeholder game_ids,
# rebuilds rolling features after new box scores land, settles before AND after
# logging new picks so anything for already-final games settles in the same run.
#
# Resilience (E3): we deliberately do NOT use `set -e`. A single failed ingest or
# feature step (a transient DB blip, an API timeout) must not abort the whole run
# before pick generation — that's how entire days of picks were lost. Each failure
# is logged via the ERR trap and the run continues; a final check reports whether
# picks actually landed.
set -uo pipefail
FAILURES=0
trap 'rc=$?; FAILURES=$((FAILURES+1)); echo "⚠️  daily.sh: step failed (rc=$rc) near line $LINENO — continuing"' ERR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# Load .env so cron (which has no environment) picks up RAILWAY_DATABASE_URL
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

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

# Use Railway DB for daily operations if configured
if [ -n "${RAILWAY_DATABASE_URL:-}" ]; then
    export DATABASE_URL="$RAILWAY_DATABASE_URL"
    echo "Using Railway DB for daily operations"
fi

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
# When writing to Railway (remote DB), cap at 30 games to avoid backfilling
# years of history. Railway only needs recent data for inference.
echo "--- Box scores ---"
if [ -n "${RAILWAY_DATABASE_URL:-}" ] && [ "$DATABASE_URL" = "$RAILWAY_DATABASE_URL" ]; then
    python -m props.ingest.mlb_boxscores --limit 30
else
    python -m props.ingest.mlb_boxscores
fi
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
python -m props.features.nba_teammate_absence
python -m props.features.nba_basketball_iq
python -m props.features.nba_play_types

echo "--- WNBA rolling features ---"
python -m props.features.wnba_rolling
python -m props.features.wnba_basketball_iq

echo "--- NHL rolling features ---"
python -m props.features.nhl_rolling
python -m props.features.nhl_advanced_stats

echo "--- MLB rolling features ---"
python -m props.features.mlb_rolling
python -m props.features.mlb_opposing_pitcher
python -m props.features.mlb_opposing_lineup
python -m props.features.mlb_batter_vs_pitcher
python -m props.features.mlb_advanced_stats

# ── 4. Live data refreshes ───────────────────────────────────────────────────
echo "--- Injuries ---"
python -m props.ingest.injuries

echo "--- PrizePicks lines ---"
python -m props.ingest.prizepicks --sport nba  || true
python -m props.ingest.prizepicks --sport mlb  || true
python -m props.ingest.prizepicks --sport wnba || true
python -m props.ingest.prizepicks --sport nhl  || true

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

# ── 8a. Retention prune (E2) — keep the volume from refilling ────────────────
echo "--- Retention prune (snapshots > 45d) ---"
python -m props.maintenance.prune --days 45

# ── 8b. Health check + alert (E11) ───────────────────────────────────────────
# Report whether picks actually landed and ping Discord on trouble, so a silent
# outage (DB down, 0 picks, failed steps) surfaces immediately instead of hours
# later.
PICKS_TODAY=$(python - <<'PY'
from props.utils.db import session_scope
from sqlalchemy import text
try:
    with session_scope() as s:
        n = s.execute(text(
            "SELECT COUNT(*) FROM picks pk "
            "WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date "
            "      = (NOW() AT TIME ZONE 'America/Los_Angeles')::date")).scalar()
    print(int(n))
except Exception:
    print(-1)
PY
)
echo "Health: picks_today=$PICKS_TODAY  step_failures=$FAILURES"
if [ -n "${DISCORD_WEBHOOK_URL:-}" ] && { [ "${PICKS_TODAY:-0}" -le 0 ] || [ "$FAILURES" -gt 0 ]; }; then
    MSG="⚠️ prop-edge daily $TODAY — picks=$PICKS_TODAY, step_failures=$FAILURES. Check logs/daily_$TODAY.log"
    curl -s -m 10 -H "Content-Type: application/json" \
         -d "{\"content\": \"$MSG\"}" "$DISCORD_WEBHOOK_URL" >/dev/null 2>&1 || true
fi

# ── 9. Rotate old logs (keep 30 days) ────────────────────────────────────────
find "$LOG_DIR" -name "daily_*.log" -mtime +30 -delete 2>/dev/null || true

echo ""
echo "======================================================"
echo "  Done: $(date)"
echo "  Dashboard: streamlit run ui/dashboard.py"
echo "======================================================"
