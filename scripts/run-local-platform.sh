#!/usr/bin/env bash
# Run BK ClinicQ on the local BTK platform (infra edge nginx → this product nginx → app).
#
# Default: Docker stack with product nginx + app on 127.0.0.1:APP_PORT.
# Gateway owns host :80; this nginx is the downstream on :8011.
# See: ../infra/docs/ARCHITECTURE/local-platform.md
#
# Prerequisites:
#   1. Gateway platform up:  cd ../infra && ./scripts/run-local-platform.sh
#   2. /etc/hosts: 127.0.0.1 infra.btk.localhost properties.btk.localhost
#   3. .env present (from .env.example) and .venv with deps (for migrations)
#
# Usage (from properties repo root):
#   ./scripts/run-local-platform.sh              # docker: nginx + app on :8011
#   ./scripts/run-local-platform.sh --uvicorn    # host uvicorn (no product nginx)
#   ./scripts/run-local-platform.sh --down       # stop docker platform stack
#   ./scripts/run-local-platform.sh --no-migrate
#   ./scripts/run-local-platform.sh --port 5434
#   ./scripts/run-local-platform.sh --recreate   # force-recreate Docker containers
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PROJECT_SLUG="${PROJECT_SLUG:-properties}"
DISPLAY_NAME="${DISPLAY_NAME:-BK ClinicQ}"
APP_PORT="${APP_PORT:-8011}"
DB_SCHEMA="${DB_SCHEMA:-properties}"
REPO_URL="${REPO_URL:-https://github.com/Billykat7/clinicq}"
BASE_DOMAIN="${BASE_DOMAIN:-btk.localhost}"
DOMAIN="${DOMAIN:-properties.${BASE_DOMAIN}}"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://127.0.0.1:8000}"
REGISTRY_DEPLOY_TOKEN="${REGISTRY_DEPLOY_TOKEN:-local-dev-registry-deploy-token}"
INFRA_ROOT="${INFRA_ROOT:-}"
PG_PORT="${PG_PORT:-5432}"
USE_DOCKER=1
DO_DOWN=0
DO_MIGRATE=1
DETACH=0
RECREATE=0

USAGE="Usage: ./scripts/run-local-platform.sh [options]

  Join the local gateway platform.
  Default: Docker product nginx + app on 127.0.0.1:${APP_PORT}
  (infra edge :80 → this nginx → app). Migrations use shared DB btk + schema ${DB_SCHEMA}.

Options:
  --docker         Docker nginx + app (default)
  --uvicorn        Run uvicorn on the host instead of Docker
  --recreate       Force-recreate Docker containers (volumes kept)
  --down           Stop docker platform compose for this product
  --detach         With --uvicorn: background the process (default: foreground)
  --no-migrate     Skip alembic upgrade
  --port PORT      Platform Postgres host port (default 5432; same as infra)
  --infra-root DIR Path to infra repo (default: sibling ../infra)
  -h, --help       Show this help
"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docker) USE_DOCKER=1; shift ;;
    --uvicorn) USE_DOCKER=0; shift ;;
    --recreate) RECREATE=1; shift ;;
    --down) DO_DOWN=1; shift ;;
    --detach) DETACH=1; shift ;;
    --no-migrate) DO_MIGRATE=0; shift ;;
    --port|--pg-port)
      PG_PORT="${2:?$1 requires a value}"
      if ! [[ "$PG_PORT" =~ ^[0-9]+$ ]] || ((PG_PORT < 1 || PG_PORT > 65535)); then
        echo "error: --port must be an integer between 1 and 65535" >&2
        exit 1
      fi
      shift 2
      ;;
    --port=*|--pg-port=*)
      PG_PORT="${1#*=}"
      if ! [[ "$PG_PORT" =~ ^[0-9]+$ ]] || ((PG_PORT < 1 || PG_PORT > 65535)); then
        echo "error: --port must be an integer between 1 and 65535" >&2
        exit 1
      fi
      shift
      ;;
    --infra-root)
      INFRA_ROOT="${2:?--infra-root requires a value}"
      shift 2
      ;;
    --infra-root=*)
      INFRA_ROOT="${1#*=}"
      shift
      ;;
    -h|--help)
      echo "$USAGE"
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      echo "$USAGE" >&2
      exit 1
      ;;
  esac
done

resolve_infra_root() {
  if [[ -n "$INFRA_ROOT" ]]; then
    INFRA_ROOT="$(cd "$INFRA_ROOT" && pwd)"
  elif [[ -d "$REPO_ROOT/../infra/scripts/cd" ]]; then
    INFRA_ROOT="$(cd "$REPO_ROOT/../infra" && pwd)"
  else
    echo "error: cannot find infra repo. Set INFRA_ROOT or pass --infra-root." >&2
    exit 1
  fi
  if [[ ! -f "$INFRA_ROOT/scripts/cd/register-project.sh" ]]; then
    echo "error: infra scripts missing under $INFRA_ROOT" >&2
    exit 1
  fi
}

COMPOSE_PLATFORM=(
  docker compose
  -f infra/docker/docker-compose.platform.yml
  --project-directory infra/docker
)

if [[ "$DO_DOWN" -eq 1 ]]; then
  echo "==> Stopping BK ClinicQ platform compose (btk-clinicq)..."
  # Compose interpolates the file even for `down`; DATABASE_URL is required
  # there but unused when stopping, so provide a placeholder if unset.
  DATABASE_URL="${DATABASE_URL:-unused}" "${COMPOSE_PLATFORM[@]}" down
  exit 0
fi

resolve_infra_root

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "==> Created .env from .env.example (edit secrets as needed)."
  else
    echo "error: no .env or .env.example at $REPO_ROOT" >&2
    exit 1
  fi
fi

# Preserve caller/CLI platform identity before .env can overwrite DOMAIN etc.
_PLATFORM_SLUG="$PROJECT_SLUG"
_PLATFORM_PORT="$APP_PORT"
_PLATFORM_SCHEMA="$DB_SCHEMA"
_PLATFORM_DOMAIN="$DOMAIN"
_PLATFORM_BASE="$BASE_DOMAIN"
_PLATFORM_GW="$GATEWAY_BASE_URL"
_PLATFORM_TOKEN="$REGISTRY_DEPLOY_TOKEN"

# shellcheck disable=SC1091
set +u
set -a
# Disable nounset while sourcing — .env may reference optional ${VARS}.
source .env
set +a
set -u

# Platform local overrides win over product .env (prod domains, ports, …).
export PROJECT_SLUG="$_PLATFORM_SLUG"
export DISPLAY_NAME
export APP_PORT="$_PLATFORM_PORT"
export DB_SCHEMA="$_PLATFORM_SCHEMA"
export REPO_URL
export BASE_DOMAIN="$_PLATFORM_BASE"
export DOMAIN="$_PLATFORM_DOMAIN"
export GATEWAY_BASE_URL="$_PLATFORM_GW"
export REGISTRY_DEPLOY_TOKEN="$_PLATFORM_TOKEN"
export TRUST_PROXY_HEADERS=true
export PUBLIC_URL_SCHEME=http
export SUBDOMAIN="$DOMAIN"
export SCHEMA_NAME="$DB_SCHEMA"

# Shared platform Postgres + properties schema (display matches alembic-upgrade.sh; same as infra).
export DB_HOST=127.0.0.1
export DB_PORT="$PG_PORT"
export DB_NAME=btk
export DB_USER="${DB_USER:-btk_user}"
export DB_PASSWORD="${DB_PASSWORD:-F9v#3rPq!T2mLd8X}"
DEFAULT_PLATFORM_DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PG_PORT}/btk"
export DATABASE_URL="${PLATFORM_DATABASE_URL:-$DEFAULT_PLATFORM_DB_URL}"
DOCKER_DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@host.docker.internal:${PG_PORT}/btk"

if [[ "$PG_PORT" != "5432" ]]; then
  echo "==> Using Postgres host port ${PG_PORT} (DATABASE_URL → 127.0.0.1:${PG_PORT}/btk)"
fi

ensure_venv() {
  if [[ -n "${VIRTUAL_ENV:-}" ]] && command -v python >/dev/null 2>&1; then
    return 0
  fi
  if [[ ! -f .venv/bin/activate ]]; then
    echo "error: .venv missing. Create with: python3.14 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
}

wait_health() {
  local url="$1"
  local label="$2"
  local attempt=0
  local max_attempts="${3:-90}"
  echo "==> Waiting for ${label} (${url})..."
  until curl -sf "$url" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if ((attempt >= max_attempts)); then
      echo "error: ${label} not healthy within ${max_attempts}s" >&2
      return 1
    fi
    sleep 1
  done
  echo "==> ${label} is healthy"
}

register_and_go_live() {
  export STATUS=deploying
  export IMAGE_REF="${IMAGE_REF:-properties:local-platform}"
  export HEALTH_OK=true
  export DEPLOY_STATUS=succeeded
  export DEPLOYED_BY="${DEPLOYED_BY:-local-platform}"
  export COMMIT_SHA="${COMMIT_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo local)}"

  echo "==> Registering ${PROJECT_SLUG} with gateway (${GATEWAY_BASE_URL})..."
  bash "$INFRA_ROOT/scripts/cd/register-project.sh"

  echo "==> Reloading gateway edge nginx..."
  bash "$INFRA_ROOT/scripts/cd/reload-gateway-nginx.sh" || {
    echo "warn: nginx-reload API failed; try re-render from infra (see local-platform.md)" >&2
  }

  echo "==> Pushing health → live..."
  bash "$INFRA_ROOT/scripts/cd/push-health.sh"
  bash "$INFRA_ROOT/scripts/cd/post-deploy-event.sh"
}

echo "==> Checking gateway at ${GATEWAY_BASE_URL}/health ..."
if ! curl -sf "${GATEWAY_BASE_URL}/health" >/dev/null; then
  echo "error: gateway is not reachable at ${GATEWAY_BASE_URL}" >&2
  echo "  Start it first:  cd ${INFRA_ROOT} && ./scripts/run-local-platform.sh" >&2
  exit 1
fi
echo "==> Gateway is up"

ensure_venv
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# Clear cached settings so DATABASE_URL / DB_SCHEMA from this shell win.
python -c "from src.core.config import get_settings; get_settings.cache_clear()" 2>/dev/null || true

if [[ "$DO_MIGRATE" -eq 1 ]]; then
  echo "==> Running BK ClinicQ migrations (schema ${DB_SCHEMA})..."
  ./scripts/db/alembic-upgrade.sh
fi

APP_PID=""
cleanup() {
  if [[ -n "${APP_PID}" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    echo ""
    echo "==> Stopping BK ClinicQ (pid ${APP_PID})..."
    kill "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "$USE_DOCKER" -eq 1 ]]; then
  RECREATE_ARGS=()
  if [[ "$RECREATE" -eq 1 ]]; then
    RECREATE_ARGS=(--force-recreate)
    echo "==> --recreate: containers will be force-recreated (volumes kept)."
  fi
  echo "==> Starting BK ClinicQ Docker (nginx + app) on 127.0.0.1:${APP_PORT}..."
  # ${arr[@]+...} keeps bash 3.2 + set -u happy when the array is empty.
  DATABASE_URL="$DOCKER_DATABASE_URL" "${COMPOSE_PLATFORM[@]}" up -d --build \
    ${RECREATE_ARGS[@]+"${RECREATE_ARGS[@]}"}
  # Gate on liveness (dependency-free); readiness /health also needs migrations at head.
  wait_health "http://127.0.0.1:${APP_PORT}/health/live" "BK ClinicQ (via product nginx)"
  register_and_go_live
  trap - EXIT INT TERM
  echo ""
  echo "BK ClinicQ is live on the local platform."
  echo "  Path:     browser → infra edge :80 → product nginx :${APP_PORT} → app"
  echo "  Direct:   http://127.0.0.1:${APP_PORT}/health"
  echo "  Via edge: http://${DOMAIN}/health"
  echo "  Dashboard: http://infra.${BASE_DOMAIN}/  (or ${GATEWAY_BASE_URL}/)"
  echo "Stop: ./scripts/run-local-platform.sh --down"
  exit 0
fi

echo "==> Starting uvicorn on 127.0.0.1:${APP_PORT} (no product nginx)..."
uvicorn src.main:app --reload --host 127.0.0.1 --port "${APP_PORT}" &
APP_PID=$!

# Gate on liveness (dependency-free); readiness /health also needs migrations at head.
wait_health "http://127.0.0.1:${APP_PORT}/health/live" "BK ClinicQ"
register_and_go_live

echo ""
echo "BK ClinicQ is live on the local platform (uvicorn; infra edge → app)."
echo "  Direct:   http://127.0.0.1:${APP_PORT}/health"
echo "  Via edge: http://${DOMAIN}/health"
echo "  Dashboard: http://infra.${BASE_DOMAIN}/  (or ${GATEWAY_BASE_URL}/)"
echo ""

if [[ "$DETACH" -eq 1 ]]; then
  echo "Detached uvicorn pid ${APP_PID} (kill to stop)."
  trap - EXIT INT TERM
  exit 0
fi

echo "Foreground — Ctrl+C to stop."
wait "$APP_PID"
