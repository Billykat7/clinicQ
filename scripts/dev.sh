#!/usr/bin/env bash
#
# Local development in one command: start PostgreSQL 18 + PostGIS and Redis in Docker, wait until
# both health checks pass, then run the API on the host with auto-reload.
#
# Usage: ./scripts/dev.sh [--port PORT]      (or: make dev [PORT=8000])
#
# Needs a .env at the repo root (cp .env.example .env). Migrations stay a deliberate step: run
# `make migrate-up` after the first start, after `make db-reset`, and after pulling a migration.
# Ctrl+C stops the API; the containers keep running until `make db-down`.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  echo "Usage: $0 [--port PORT]   (default port: \$PORT or 8000)"
}

PORT="${PORT:-8000}"
while [[ $# -gt 0 ]]; do
  case $1 in
    --port)
      if [[ $# -lt 2 ]]; then
        usage >&2
        exit 1
      fi
      PORT="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "No .env found. Create one from the template first: cp .env.example .env" >&2
  exit 1
fi

# Same venv the rest of the tooling uses (see .cursor/rules/activate-venv-before-commands.mdc).
# shellcheck disable=SC1091  # the venv is created per clone, so there is no file to follow
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
  elif [[ -f venv/bin/activate ]]; then
    source venv/bin/activate
  fi
fi

if ! command -v uvicorn &>/dev/null; then
  echo "uvicorn not found. Install the dependencies first: pip install -r requirements.txt" >&2
  exit 1
fi

COMPOSE=(docker compose -f infra/docker/docker-compose.yml --project-directory infra/docker)

echo "==> Starting PostgreSQL/PostGIS and Redis (waits for both health checks)"
"${COMPOSE[@]}" up -d --wait db redis

echo "==> API on http://127.0.0.1:${PORT} with reload (Ctrl+C stops it; make db-down stops the containers)"
exec uvicorn src.main:app --reload --host 127.0.0.1 --port "$PORT"
