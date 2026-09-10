#!/usr/bin/env bash
# Start local PostgreSQL/PostGIS for development.
# Usage: ./scripts/run-local.sh   (ensure .env exists from .env.example)

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found. Copy .env.example to .env and set DATABASE_URL (and SECRET_KEY if required)."
  exit 1
fi

COMPOSE_DB=(docker compose -f infra/docker/docker-compose.db.yml --project-directory infra/docker)

echo "Starting PostgreSQL/PostGIS..."
"${COMPOSE_DB[@]}" up -d postgres

echo "Waiting for Postgres..."
until "${COMPOSE_DB[@]}" exec -T postgres pg_isready -U "${DB_USER:-btk_user}" -d "${DB_NAME:-btk}"; do
  sleep 1
done

echo "Database is ready. Next steps:"
echo "  ./scripts/db/alembic-upgrade.sh"
echo "  ./scripts/db/seed-dev-user.sh   # optional dev admin"
echo "  make run                        # or: make docker-up"
