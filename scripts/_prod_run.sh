#!/usr/bin/env bash
# One-off helper: run a python module against the PROD (Railway) DB.
# Usage: scripts/_prod_run.sh [DERIVED_BACKFILL_ALL] -m props.features.xxx
set -uo pipefail
cd "$(cd "$(dirname "$0")" && pwd)/.."
set -a; source .env; set +a
export DATABASE_URL="$RAILWAY_DATABASE_URL"
source .venv/bin/activate
python - <<'PY'
from props.utils.db import engine
h = engine.url.host or ""
assert "rlwy.net" in h or "railway" in h, f"NOT prod: {h}"
print(f"[target] {h}  (PROD)")
PY
exec "$@"
