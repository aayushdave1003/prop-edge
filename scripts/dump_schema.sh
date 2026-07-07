#!/usr/bin/env bash
# Regenerate sql/schema.sql from the live database — canonical, byte-exact pg_dump.
#
# The committed sql/schema.sql is a reference snapshot of the prod schema. The DB
# is authoritatively evolved by props/maintenance/migrate.py; this just refreshes
# the readable snapshot so it doesn't drift.
#
# Requires the pg_dump client to MATCH the server major version (Railway = PG 18).
# We run it in a pinned postgres:18 docker image — same pattern as the nightly
# backup (.github/workflows/db_backup.yml) — so no host pg_dump version juggling.
# (A host pg_dump also works IF it is v18+.)
#
# Usage:  RAILWAY_DATABASE_URL=... scripts/dump_schema.sh
#         (or it reads RAILWAY_DATABASE_URL from .env)
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${RAILWAY_DATABASE_URL:-$(grep '^RAILWAY_DATABASE_URL=' .env | cut -d= -f2-)}"
URL="${URL/+psycopg/}"   # SQLAlchemy form -> libpq form
[ -n "$URL" ] || { echo "no RAILWAY_DATABASE_URL"; exit 1; }

OUT="sql/schema.sql"
HEADER="-- =====================================================================
-- prop-edge — DATABASE SCHEMA (PostgreSQL 18)
-- AUTO-GENERATED via scripts/dump_schema.sh (pg_dump --schema-only).
-- Do not hand-edit; change the DB via props/maintenance/migrate.py then regen.
-- ====================================================================="

if command -v docker >/dev/null 2>&1; then
    BODY="$(docker run --rm -e U="$URL" postgres:18 \
        sh -c 'pg_dump "$U" --schema-only --no-owner --no-privileges --schema=public')"
elif command -v pg_dump >/dev/null 2>&1; then
    BODY="$(pg_dump "$URL" --schema-only --no-owner --no-privileges --schema=public)"
else
    echo "need docker or a PG18 pg_dump on PATH"; exit 1
fi

printf '%s\n\n%s\n' "$HEADER" "$BODY" > "$OUT"
echo "wrote $OUT ($(grep -c 'CREATE TABLE' "$OUT") tables)"
