#!/usr/bin/env bash
# Restore a prop-edge pg_dump snapshot (custom format, from the db-backup workflow)
# into a target Postgres. This is the guarded counterpart to db_backup.yml.
#
#   ./scripts/restore_db.sh <dump-file> <target-DATABASE_URL> [--force]
#
# The dump can come from either offsite copy that db_backup.yml maintains:
#
#   • GitHub release (default blast radius):
#       gh release download db-backup-2026-06-14 --repo aayushdave1003/prop-edge
#       ./scripts/restore_db.sh prop-edge-2026-06-14.dump "$TARGET"
#
#   • S3 / R2 (the P0.6 offsite copy):
#       aws s3 cp s3://$BACKUP_S3_BUCKET/backups/prop-edge-2026-06-14.dump . \
#         ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"}   # R2 needs the endpoint
#       ./scripts/restore_db.sh prop-edge-2026-06-14.dump "$TARGET"
#
# !! DESTRUCTIVE to the target !! --clean --if-exists DROPS existing objects
# before recreating them — it OVERWRITES the target database. By default you must
# type the target host to confirm; pass --force (or set RESTORE_FORCE=1) to skip
# the prompt for non-interactive use (CI restore-test). NEVER --force at a prod URL.
#
# TESTED-RESTORE PATH (do this once so the backup is proven, not just present):
#   createdb -h localhost restore_check
#   ./scripts/restore_db.sh prop-edge-2026-06-14.dump postgresql://localhost/restore_check
#   # ...then sanity-check row counts, and drop it: dropdb -h localhost restore_check
#
# Needs pg_restore on PATH (brew install libpq / apt install postgresql-client),
# version >= the dump's server (Railway = PG 18). Accepts the SQLAlchemy URL form
# too (strips the +psycopg driver tag).
set -euo pipefail

FORCE="${RESTORE_FORCE:-0}"
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; while [[ $# -gt 0 ]]; do POSITIONAL+=("$1"); shift; done ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
set -- "${POSITIONAL[@]:-}"

DUMP="${1:-}"
TARGET="${2:-}"
if [[ -z "$DUMP" || -z "$TARGET" ]]; then
  echo "usage: $0 <dump-file> <target-DATABASE_URL> [--force]" >&2
  exit 2
fi
[[ -f "$DUMP" ]] || { echo "dump file not found: $DUMP" >&2; exit 2; }
command -v pg_restore >/dev/null 2>&1 || {
  echo "pg_restore not on PATH — install postgresql-client (brew install libpq / apt install postgresql-client)." >&2
  exit 2
}

TARGET="${TARGET/+psycopg/}"                       # postgresql+psycopg:// → postgresql://
HOST="$(printf '%s' "$TARGET" | sed -E 's#^[^@]+@##; s#[:/].*$##')"

echo "About to restore:"
echo "  dump   : $DUMP"
echo "  target : $TARGET   (host: $HOST)"
echo "!! This OVERWRITES the target: it DROPS and recreates objects in that database. !!"
if [[ "$FORCE" == "1" ]]; then
  echo "--force set — skipping confirmation (make sure this is NOT a prod URL)."
else
  read -r -p "Type the target host ('$HOST') to confirm: " CONFIRM
  [[ "$CONFIRM" == "$HOST" ]] || { echo "host mismatch — aborting."; exit 1; }
fi

pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
  -d "$TARGET" "$DUMP"
echo "✅ restore complete."
