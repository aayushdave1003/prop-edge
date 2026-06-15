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

# Activate venv if present (local cron). On CI (GitHub Actions) there's no venv;
# deps are installed into the system Python instead.
[ -d .venv ] && source .venv/bin/activate || true

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

# ── 0. Canonical team rosters ────────────────────────────────────────────────
# Seed/refresh each league's FULL team list with correct abbreviations, so the
# UI shows all teams (MLB 30 / NHL 32) and not just the ones with recent games.
# The schedule ingest otherwise only creates a team the first time it plays, and
# mangles MLB abbreviations (name[:3] → LOS/SAN collisions). Cheap + idempotent.
echo "--- Team rosters (MLB + NHL) ---"
python -m props.ingest.mlb_teams || echo "WARN: mlb_teams sync failed"
python -m props.ingest.nhl_teams || echo "WARN: nhl_teams sync failed"
# Player → current team from official rosters (fixes trades; MLB/NHL by id,
# NBA/WNBA by name). Update-only, never clears — safe + idempotent.
echo "--- Roster sync (current_team_id) ---"
python -m props.ingest.rosters || echo "WARN: roster sync failed"

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
# When writing to Railway (remote DB), cap the batch to avoid backfilling years
# of history. The universe is already bounded to the last 5 days
# (get_unprocessed_games since_days=5 ≈ 75 MLB games), so the cap only needs to
# clear a full 5-day window — 30 was too low and left recent finals un-boxscored,
# which made settle void real picks as phantom DNPs. 90 covers the window.
echo "--- Box scores ---"
if [ -n "${RAILWAY_DATABASE_URL:-}" ] && [ "$DATABASE_URL" = "$RAILWAY_DATABASE_URL" ]; then
    python -m props.ingest.mlb_boxscores --limit 90
else
    python -m props.ingest.mlb_boxscores
fi
python -m props.ingest.nba_boxscores
python -m props.ingest.wnba_boxscores
python -m props.ingest.nhl_boxscores

# ── 2b. MLB ballpark weather (Open-Meteo, free) ──────────────────────────────
# Wind blowing out drives offense (validated: 65% over-rate vs 43% calm/in).
# Fetches today's games + backfills a few recent days for settled-pick coverage.
echo "--- MLB weather ---"
python -m props.ingest.mlb_weather --since-days 3 || echo "WARN: mlb_weather failed"
python -m props.features.mlb_weather_features --since-days 5 || echo "WARN: mlb_weather_features failed"

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
python -m props.features.mlb_batter_sos     # opponent-adjusted (needs opposing_pitcher first)
python -m props.features.mlb_opposing_lineup
python -m props.features.mlb_batter_vs_pitcher
python -m props.features.mlb_advanced_stats

# ── 4. Live data refreshes ───────────────────────────────────────────────────
# PrizePicks blocks datacenter IPs, so the scrape only runs here when a
# residential proxy is configured (PRIZEPICKS_PROXY) — then the pipeline is
# fully self-contained on GitHub Actions. Without a proxy this is skipped and
# the Mac cron (scripts/scrape_lines.sh) owns scraping; ingest_monitor flags
# stale lines if the Mac is asleep. Injuries use ESPN (datacenter-friendly).
if [ -n "${PRIZEPICKS_PROXY:-}" ]; then
    echo "--- PrizePicks lines (via proxy) ---"
    python -m props.ingest.prizepicks || echo "WARN: prizepicks scrape failed"
fi
echo "--- Injuries ---"
python -m props.ingest.injuries

# ── 5. Settle previous picks ─────────────────────────────────────────────────
echo "--- Settle yesterday's picks ---"
python -m props.picks.settle_picks

# ── 6. Generate + log today's picks ─────────────────────────────────────────
# log_picks runs predict internally (which also persists game predictions to
# games.context) and retries on transient connection drops, so we DON'T run a
# separate predict_today pass — that just doubled the load on the small Railway
# instance and the drop risk (E10).
echo "--- Generate and log today's picks ---"
python -m props.picks.log_picks --date "$TODAY"

echo "--- Confirm MLB starters (morning check) ---"
python -m props.picks.confirm_starters --date "$TODAY" || true

# ── 7. Second settle pass ────────────────────────────────────────────────────
echo "--- Second settle pass ---"
python -m props.picks.settle_picks

# ── 7a. Self-heal: auto-clear any stuck picks (cloud, no human needed) ───────
# If a transient step failure stranded picks on already-final/past games, this
# re-attempts box scores + settle so the system fixes itself unattended.
echo "--- Self-heal ---"
python -m props.maintenance.self_heal || true

# ── 7b. Closing line value (capture the close for started games) ─────────────
echo "--- Compute CLV ---"
python -m props.picks.compute_clv || true

# ── 7c. Nightly scorecard to Discord (last night's results) ──────────────────
echo "--- Discord scorecard ---"
python -m props.picks.scorecard || true

# ── 7d. Daily feature-ideas digest (something to build today) ────────────────
echo "--- Feature ideas ---"
python -m props.maintenance.feature_ideas || true

# ── 7e2. Soft-line finder (PrizePicks vs sharp market) ───────────────────────
# Surfaces PrizePicks lines the sharp market prices as +EV, independent of the
# model. Needs the odds key (live sharp odds, ~20 credits). Posts a Discord
# digest + persists to soft_lines for the dashboard.
if [ -n "${ODDS_API_KEY:-}" ]; then
    echo "--- Soft-line finder ---"
    python -m props.picks.soft_lines || true
fi

# ── 7e. Daily walk-forward backtest ──────────────────────────────────────────
# Replays the recommended-tier strategy over a rolling window of SETTLED picks
# (not the frozen market_odds the old weekly backtest needed): rec-tier W/L vs
# breakeven + trend, model calibration/drift, and a counterfactual cutoff sweep
# that checks the auto-tuner. Persists a daily snapshot + posts a Discord digest.
echo "--- Daily backtest ---"
python -m props.picks.daily_backtest --window 45 || true

# ── 8. Weekly model-vs-market backtest (Mondays) ─────────────────────────────
# This one needs the paid odds feed (market_odds); it only has signal while that
# feed is live. Kept weekly + Monday-gated so it doesn't slow the daily run.
if [ "$(date +%u)" = "1" ]; then
    # First top up market_odds for recently-final games (newest first), on a
    # strict per-run BILLED-credit budget (historical endpoints bill ~20×/call,
    # so the cap is measured in real credits, not HTTP calls). Only the handful
    # of new games each week need filling, so 800 credits/sport is ample headroom
    # yet can't dent the 100k plan (worst case 2×800/wk ≈ 6.4k/mo). Keeps the
    # weekly model-vs-market backtest current. Only runs when a key is configured.
    if [ -n "${ODDS_API_KEY:-}" ]; then
        echo "--- Weekly market_odds refresh (budgeted) ---"
        SINCE_21=$(date -v-21d +%Y-%m-%d 2>/dev/null || date -d '21 days ago' +%Y-%m-%d)
        python -m props.ingest.historical_odds --sport nba --since "$SINCE_21" \
               --recent-first --max-requests 800 || true
        python -m props.ingest.historical_odds --sport mlb --since "$SINCE_21" \
               --recent-first --max-requests 800 || true
    fi

    echo "--- Weekly model-vs-market backtest (Monday) ---"
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

# ── 8c. Ingest health monitor ────────────────────────────────────────────────
# Checks the upstream ingest tables (lines fresh + slate not thin, recent final
# games have box scores, injuries not cold) and pings Discord on anomalies —
# catches a silently broken scrape/ingest before it zeroes out future picks.
echo "--- Ingest monitor ---"
python -m props.maintenance.ingest_monitor || true

# ── 8d. Dashboard perf / uptime monitor ──────────────────────────────────────
# A synthetic check beyond /_stcore/health: times the health endpoint AND a real
# render, pinging Discord if the app is down or render latency blows past the
# threshold (a wedged container can stay "healthy" but unusable).
echo "--- Dashboard monitor ---"
python -m props.ops.dashboard_monitor || true

# ── 8e. Cost / usage snapshot ────────────────────────────────────────────────
# Odds API credits, scrape volume, pipeline freshness, DB growth — logged so a
# blow-up (quota draining, DB ballooning) is visible in the run log.
echo "--- Usage snapshot ---"
python -m props.ops.usage || true

# ── 8f. Data-accuracy audit ──────────────────────────────────────────────────
# Verifies reference data is truthful: full team rosters per league, no colliding
# abbreviations, no junk/placeholder games leaking into views. Discord-alerts on
# anomalies so "accurate" is checked continuously, not by eye.
echo "--- Data audit ---"
python -m props.ops.data_audit || true

# ── 8g. Feature-drift monitor ─────────────────────────────────────────────────
# Flags a model feature whose upstream populating broke (high-gain feature gone
# sparse) — the silent-signal-break failure mode.
echo "--- Feature drift ---"
python -m props.ops.feature_drift || true

# ── 9. Rotate old logs (keep 30 days) ────────────────────────────────────────
find "$LOG_DIR" -name "daily_*.log" -mtime +30 -delete 2>/dev/null || true

echo ""
echo "======================================================"
echo "  Done: $(date)"
echo "  Dashboard: streamlit run ui/dashboard.py"
echo "======================================================"
