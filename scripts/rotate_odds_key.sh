#!/usr/bin/env bash
# Rotate the Odds API key safely.
#
# Prompts for the new key with the input HIDDEN (never echoed, never written to
# shell history or this repo), updates BOTH places the key must live — the local
# .env and the GitHub Actions secret (what the cloud pipeline uses) — then
# verifies the key against The Odds API. Run it from anywhere:
#
#     bash scripts/rotate_odds_key.sh
#
# Get a new key by regenerating it at https://the-odds-api.com (your account).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

printf "Paste the new Odds API key (input hidden), then press Enter: "
IFS= read -rs NEWKEY
echo
if [ -z "${NEWKEY:-}" ]; then
    echo "✗ No key entered — nothing changed. Re-run and paste the key at the prompt."
    exit 1
fi
echo "Captured ${#NEWKEY} characters."

# 1. GitHub secret (the cloud pipeline reads this) — value piped in, not in argv.
if printf %s "$NEWKEY" | gh secret set ODDS_API_KEY; then
    echo "✓ GitHub secret ODDS_API_KEY updated"
else
    echo "✗ Failed to set the GitHub secret (is 'gh' authenticated? run 'gh auth status')"
fi

# 2. Local .env (only used for local runs).
if grep -q '^ODDS_API_KEY=' .env 2>/dev/null; then
    sed -i '' "s|^ODDS_API_KEY=.*|ODDS_API_KEY=$NEWKEY|" .env
else
    printf 'ODDS_API_KEY=%s\n' "$NEWKEY" >> .env
fi
echo "✓ local .env updated"

# 3. Verify against the free /sports endpoint (costs 0 requests).
read -r CODE REMAIN < <(curl -s -D - -o /dev/null \
    "https://api.the-odds-api.com/v4/sports/?apiKey=$NEWKEY" 2>/dev/null \
    | awk 'tolower($1) ~ /^http/ {c=$2} tolower($1) ~ /x-requests-remaining/ {r=$2} END {print c, r}')
unset NEWKEY
if [ "${CODE:-}" = "200" ]; then
    echo "✓ key valid (HTTP 200) — ${REMAIN:-?} requests remaining. Feed is live."
else
    echo "✗ key check returned HTTP ${CODE:-?} — double-check you pasted the right key."
    exit 1
fi
