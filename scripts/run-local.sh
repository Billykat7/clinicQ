#!/usr/bin/env bash
# Start the local PostgreSQL/PostGIS and Redis containers, wait until both are healthy, and print
# the next steps. `make db-up` does the same without the checklist; `make dev` also runs the API.
# Usage: ./scripts/run-local.sh   (ensure .env exists: cp .env.example .env)

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found. Copy .env.example to .env (cp .env.example .env) and adjust ports if needed."
  exit 1
fi

COMPOSE=(docker compose -f infra/docker/docker-compose.yml --project-directory infra/docker)

echo "Starting PostgreSQL/PostGIS and Redis (waits for both health checks)..."
"${COMPOSE[@]}" up -d --wait db redis

echo "Database and Redis are ready. Next steps:"
echo "  ./scripts/db/alembic-upgrade.sh"
echo "  ./scripts/db/seed-dev-user.sh   # optional dev admin"
echo "  make run                        # or: make docker-up"
