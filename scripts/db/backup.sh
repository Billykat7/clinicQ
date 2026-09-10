#!/usr/bin/env bash
set -euo pipefail

# Scheduled, encrypted logical backup of this product's PostgreSQL schema.
#
# BK ClinicQ keeps its tables in the `clinicq` schema of the shared platform
# database (see DbSchema / DB_SCHEMA). This dumps ONLY that schema — never the whole
# cluster, never a sibling product's schema — encrypts the dump off-host with age,
# optionally ships it to object storage, prunes what has aged out, and pings a
# dead-man's-switch monitor so a SILENT failure still raises an alert.
#
# Usage:
#   ./scripts/db/backup.sh
#
# Configuration (env, or `.env` at the repo root):
#   DATABASE_URL            (required) libpq URL of the platform database.
#   DB_SCHEMA               Schema to dump. Default: clinicq.
#   BACKUP_AGE_RECIPIENT    (required) age recipient public key (age1...) the dump is
#                           encrypted to. Use BACKUP_AGE_RECIPIENTS_FILE for several.
#   BACKUP_AGE_RECIPIENTS_FILE  File of age recipients, one per line (alt to above).
#   BACKUP_DIR              Local staging dir. Default: <repo>/var/backups.
#   BACKUP_S3_URI           Off-host destination, e.g. s3://btk-backups/clinicq/.
#                           When set, the encrypted dump is copied there with `aws s3 cp`.
#   BACKUP_RETENTION_DAYS   Prune local dumps older than this. Default: 14.
#                           (Off-host retention is enforced by the bucket lifecycle policy.)
#   BACKUP_HEARTBEAT_URL    Dead-man's-switch monitor (e.g. healthchecks.io). The run
#                           pings <url>/start, <url> on success, <url>/fail on failure.
#                           If a scheduled run never pings, the monitor alerts a human.
#
# The dump is pg_dump custom format (-Fc) so restore.sh can drive pg_restore with
# --jobs and selective options. Nothing unencrypted is ever written off the staging dir.

RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

# Load repo .env for DATABASE_URL / backup settings, but let variables already present
# in the environment win — an operator overriding on the command line beats the file.
if [ -f ".env" ]; then
  _pre_env="$(mktemp)"; export -p > "${_pre_env}"
  set +u; set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a; set -u
  # shellcheck disable=SC1090
  . "${_pre_env}"; rm -f "${_pre_env}"
fi

DB_SCHEMA="${DB_SCHEMA:-clinicq}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/var/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
HEARTBEAT_URL="${BACKUP_HEARTBEAT_URL:-}"

# Heartbeat ping helper — best-effort, never fails the backup on a monitor hiccup.
ping_heartbeat() {
  local suffix="${1:-}"
  [ -n "${HEARTBEAT_URL}" ] || return 0
  curl -fsS -m 10 --retry 3 "${HEARTBEAT_URL}${suffix}" -o /dev/null 2>/dev/null || true
}

# Any error (set -e) or unexpected exit pings the monitor's /fail endpoint so the
# absence of a success ping is never mistaken for a healthy, silent backup.
on_error() {
  local code=$?
  echo -e "${RED}❌ Backup failed (exit ${code}). Alerting monitor.${NC}" >&2
  ping_heartbeat "/fail"
  exit "${code}"
}
trap on_error ERR

echo -e "${BLUE}🗄️  BK ClinicQ — encrypted schema backup${NC}"
echo ""

# --- Preconditions ---------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ]; then
  echo -e "${RED}❌ DATABASE_URL is not set (env or .env).${NC}" >&2
  exit 1
fi
for tool in pg_dump age curl; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo -e "${RED}❌ Required tool not found: ${tool}${NC}" >&2
    exit 1
  fi
done

# age recipients: an explicit key, or a recipients file. Refuse to write a dump we
# cannot encrypt — an unencrypted lease/ledger dump must never leave the host.
AGE_ARGS=()
if [ -n "${BACKUP_AGE_RECIPIENTS_FILE:-}" ]; then
  if [ ! -f "${BACKUP_AGE_RECIPIENTS_FILE}" ]; then
    echo -e "${RED}❌ BACKUP_AGE_RECIPIENTS_FILE not found: ${BACKUP_AGE_RECIPIENTS_FILE}${NC}" >&2
    exit 1
  fi
  AGE_ARGS+=("-R" "${BACKUP_AGE_RECIPIENTS_FILE}")
elif [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  AGE_ARGS+=("-r" "${BACKUP_AGE_RECIPIENT}")
else
  echo -e "${RED}❌ No age recipient set. Set BACKUP_AGE_RECIPIENT or BACKUP_AGE_RECIPIENTS_FILE.${NC}" >&2
  echo -e "${YELLOW}   A backup that cannot be encrypted must not be written.${NC}" >&2
  exit 1
fi

ping_heartbeat "/start"

# --- Dump ------------------------------------------------------------------
mkdir -p "${BACKUP_DIR}"
# SAST (Africa/Johannesburg) timestamp with offset — business "when", still sortable.
STAMP="$(TZ=Africa/Johannesburg date +%Y%m%dT%H%M%S%z)"
OUT="${BACKUP_DIR}/clinicq-${DB_SCHEMA}-${STAMP}.dump.age"
TMP="${OUT}.partial"

# Redact any password when echoing the target.
SAFE_URL="$(printf '%s' "${DATABASE_URL}" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#')"
echo -e "${GREEN}✓${NC} Source : ${SAFE_URL}"
echo -e "${GREEN}✓${NC} Schema : ${DB_SCHEMA}"
echo -e "${GREEN}✓${NC} Output : ${OUT}"
echo ""
echo -e "${YELLOW}⏳ pg_dump → age (encrypting)…${NC}"

# pg_dump custom format for exactly this schema, streamed straight into age. Piping
# means the plaintext dump never touches disk. PIPESTATUS guards the pg_dump leg so a
# dump failure is not masked by age succeeding on a truncated stream.
set -o pipefail
pg_dump --format=custom --no-owner --no-privileges \
  --schema="${DB_SCHEMA}" "${DATABASE_URL}" \
  | age "${AGE_ARGS[@]}" -o "${TMP}"
mv "${TMP}" "${OUT}"

SIZE_BYTES="$(wc -c < "${OUT}" | tr -d ' ')"
SIZE_HUMAN="$(du -h "${OUT}" | cut -f1)"
echo -e "${GREEN}✓ Wrote ${SIZE_HUMAN} (${SIZE_BYTES} bytes)${NC}"

# --- Off-host copy ---------------------------------------------------------
if [ -n "${BACKUP_S3_URI:-}" ]; then
  if ! command -v aws >/dev/null 2>&1; then
    echo -e "${RED}❌ BACKUP_S3_URI is set but aws CLI is not installed.${NC}" >&2
    exit 1
  fi
  DEST="${BACKUP_S3_URI%/}/$(basename "${OUT}")"
  echo -e "${YELLOW}⏳ Uploading off-host → ${DEST}${NC}"
  aws s3 cp "${OUT}" "${DEST}"
  echo -e "${GREEN}✓ Off-host copy stored (already encrypted at rest).${NC}"
else
  echo -e "${YELLOW}⚠️  BACKUP_S3_URI not set — dump staged locally only.${NC}"
fi

# --- Retention -------------------------------------------------------------
# Local staging retention only; the object store's lifecycle policy is the off-host
# enforcer (documented in docs/CICD/BACKUP-RESTORE.md).
echo ""
echo -e "${BLUE}🧹 Pruning local dumps older than ${BACKUP_RETENTION_DAYS} day(s)…${NC}"
PRUNED="$(find "${BACKUP_DIR}" -type f -name 'clinicq-*.dump.age' \
  -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete | wc -l | tr -d ' ')"
echo -e "${GREEN}✓ Pruned ${PRUNED} old dump(s).${NC}"

ping_heartbeat ""
echo ""
echo -e "${GREEN}✅ Backup complete.${NC}"
