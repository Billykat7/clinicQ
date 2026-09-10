#!/usr/bin/env bash
set -euo pipefail

# Restore an encrypted schema dump (from backup.sh) into a SCRATCH database and
# verify it — the rehearsal that turns "we have backups" into "we can restore".
#
# This never touches the live database. It decrypts the .age dump with your age
# identity, restores it into a throwaway target you name, and prints per-table row
# counts so a human can eyeball that the data actually came back.
#
# Usage:
#   ./scripts/db/restore.sh <dump.age> [--identity <age-key-file>] \
#       [--target <scratch-db-url>] [--schema <name>] [--drop]
#
# Arguments / options:
#   <dump.age>          Encrypted dump produced by backup.sh (may be a local path;
#                       pull from object storage first if the copy lives off-host).
#   --identity FILE     age identity (private key) file. Default: $BACKUP_AGE_IDENTITY.
#   --target URL        libpq URL of the SCRATCH database to restore into.
#                       Default: $RESTORE_TARGET_URL. MUST NOT be the production URL.
#   --schema NAME       Schema expected in the dump. Default: $DB_SCHEMA or `clinicq`.
#   --drop              DROP SCHEMA <name> CASCADE on the target before restoring, for a
#                       clean-room rehearsal. Off by default so nothing is destroyed by
#                       accident.
#
# Exit status is non-zero if decryption, restore, or the row-count read fails.

RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

# Load repo .env, but let variables already present in the environment win — the
# scratch target and identity are typically passed on the command line, and must not
# be clobbered by the live values in .env.
if [ -f ".env" ]; then
  _pre_env="$(mktemp)"; export -p > "${_pre_env}"
  set +u; set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a; set -u
  # shellcheck disable=SC1090
  . "${_pre_env}"; rm -f "${_pre_env}"
fi

DUMP=""
IDENTITY="${BACKUP_AGE_IDENTITY:-}"
TARGET_URL="${RESTORE_TARGET_URL:-}"
SCHEMA="${DB_SCHEMA:-clinicq}"
DROP="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --identity) IDENTITY="$2"; shift 2 ;;
    --target)   TARGET_URL="$2"; shift 2 ;;
    --schema)   SCHEMA="$2"; shift 2 ;;
    --drop)     DROP="true"; shift ;;
    -h|--help)  sed -n '3,30p' "$0"; exit 0 ;;
    -*)         echo -e "${RED}❌ Unknown option: $1${NC}" >&2; exit 1 ;;
    *)          DUMP="$1"; shift ;;
  esac
done

echo -e "${BLUE}🔁 BK ClinicQ — restore drill (scratch database)${NC}"
echo ""

# --- Preconditions ---------------------------------------------------------
for tool in pg_restore psql age; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo -e "${RED}❌ Required tool not found: ${tool}${NC}" >&2
    exit 1
  fi
done
if [ -z "${DUMP}" ] || [ ! -f "${DUMP}" ]; then
  echo -e "${RED}❌ Encrypted dump not given or not found: '${DUMP}'${NC}" >&2
  exit 1
fi
if [ -z "${IDENTITY}" ] || [ ! -f "${IDENTITY}" ]; then
  echo -e "${RED}❌ age identity file not given or not found. Use --identity or set BACKUP_AGE_IDENTITY.${NC}" >&2
  exit 1
fi
if [ -z "${TARGET_URL}" ]; then
  echo -e "${RED}❌ No scratch target. Use --target or set RESTORE_TARGET_URL.${NC}" >&2
  exit 1
fi

# Guard rail: refuse to restore into whatever DATABASE_URL points at. The drill must
# run against a scratch database, never the live one.
if [ -n "${DATABASE_URL:-}" ] && [ "${TARGET_URL}" = "${DATABASE_URL}" ]; then
  echo -e "${RED}❌ Refusing to restore: --target equals the live DATABASE_URL.${NC}" >&2
  echo -e "${YELLOW}   Point --target at a throwaway scratch database.${NC}" >&2
  exit 1
fi

SAFE_TARGET="$(printf '%s' "${TARGET_URL}" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#')"
echo -e "${GREEN}✓${NC} Dump    : ${DUMP}"
echo -e "${GREEN}✓${NC} Target  : ${SAFE_TARGET} ${YELLOW}(scratch)${NC}"
echo -e "${GREEN}✓${NC} Schema  : ${SCHEMA}"
echo ""

START_EPOCH="$(date +%s)"

# --- Decrypt ---------------------------------------------------------------
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/clinicq-restore.XXXXXX")"
trap 'rm -rf "${WORKDIR}"' EXIT
PLAIN="${WORKDIR}/restore.dump"
echo -e "${YELLOW}⏳ Decrypting dump…${NC}"
age --decrypt -i "${IDENTITY}" -o "${PLAIN}" "${DUMP}"
echo -e "${GREEN}✓ Decrypted → ${PLAIN}${NC}"

# --- Optional clean room ---------------------------------------------------
if [ "${DROP}" = "true" ]; then
  echo -e "${YELLOW}⏳ Dropping schema ${SCHEMA} on scratch target (clean room)…${NC}"
  psql "${TARGET_URL}" -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS \"${SCHEMA}\" CASCADE;"
fi

# --- Restore ---------------------------------------------------------------
echo -e "${YELLOW}⏳ pg_restore into scratch target…${NC}"
# --no-owner/--no-privileges so the dump restores cleanly under the scratch role.
# --exit-on-error surfaces a partial restore instead of quietly leaving gaps.
pg_restore --no-owner --no-privileges --exit-on-error \
  --dbname="${TARGET_URL}" "${PLAIN}"
echo -e "${GREEN}✓ Restore finished.${NC}"

DURATION=$(( $(date +%s) - START_EPOCH ))

# --- Verify: per-table row counts -----------------------------------------
echo ""
echo -e "${BLUE}🔎 Row counts in restored schema '${SCHEMA}':${NC}"
# Build a UNION ALL of count(*) over every base table in the schema, then run it, so
# the numbers are live reads of the restored data — not pg_class estimates.
COUNT_SQL="$(psql "${TARGET_URL}" -At -v ON_ERROR_STOP=1 -c "
  SELECT string_agg(
           format('SELECT %L AS table, count(*) AS rows FROM %I.%I', tablename, schemaname, tablename),
           ' UNION ALL '
         )
  FROM pg_tables
  WHERE schemaname = '${SCHEMA}';
")"

if [ -z "${COUNT_SQL}" ]; then
  echo -e "${RED}❌ No tables found in schema '${SCHEMA}' after restore.${NC}" >&2
  exit 1
fi

psql "${TARGET_URL}" -P pager=off -v ON_ERROR_STOP=1 \
  -c "SELECT * FROM (${COUNT_SQL}) t ORDER BY \"table\";"

TOTAL="$(psql "${TARGET_URL}" -At -v ON_ERROR_STOP=1 \
  -c "SELECT coalesce(sum(rows),0) FROM (${COUNT_SQL}) t;")"

echo ""
echo -e "${GREEN}✅ Restore drill OK${NC} — schema ${SCHEMA}, ${TOTAL} row(s) total, ${DURATION}s."
echo -e "${YELLOW}   Record the date, dump size, duration and counts in docs/CICD/BACKUP-RESTORE.md.${NC}"
