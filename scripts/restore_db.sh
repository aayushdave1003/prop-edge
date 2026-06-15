#!/usr/bin/env bash
# Restore a prop-edge pg_dump snapshot (custom format, from the db-backup workflow)
# into a target Postgres. This is the tested counterpart to db_backup.yml.
#
#   ./scripts/restore_db.sh <dump-file> <target-DATABASE_URL>
#
# DESTRUCTIVE to the target: --clean --if-exists drops existing objects before
# recreating them, so you must type the target host to confirm. Restore into a
# SCRATCH database first to verify a snapshot (the "tested restore path"):
#
#   createdb -h localhost restore_check
#   ./scripts/restore_db.sh prop-edge-2026-06-14.dump postgresql://localhost/restore_check
#
# Needs pg_restore on PATH (brew install libpq / apt install postgresql-client),
# version >= the dump's server. Accepts the SQLAlchemy URL form too (strips
# the +psycopg driver tag).
set -euo pipefail

DUMP="${1:-}"
TARGET="${2:-}"
if [[ -z "$DUMP" || -z "$TARGET" ]]; then
  echo "usage: $0 <dump-file> <target-DATABASE_URL>" >&2
  exit 2
fi
[[ -f "$DUMP" ]] || { echo "dump file not found: $DUMP" >&2; exit 2; }

TARGET="${TARGET/+psycopg/}"                       # postgresql+psycopg:// → postgresql://
HOST="$(printf '%s' "$TARGET" | sed -E 's#^[^@]+@##; s#[:/].*$##')"

echo "About to restore:"
echo "  dump   : $DUMP"
echo "  target : $TARGET   (host: $HOST)"
echo "This DROPS and recreates objects in the target database."
read -r -p "Type the target host ('$HOST') to confirm: " CONFIRM
[[ "$CONFIRM" == "$HOST" ]] || { echo "host mismatch — aborting."; exit 1; }

pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
  -d "$TARGET" "$DUMP"
echo "✅ restore complete."
