#!/usr/bin/env bash
set -euo pipefail

# Seed a verified dev/admin user in this project's PostgreSQL schema (clinicq by default).
# Usage:
#   ./scripts/db/seed-dev-user.sh --email admin@btk.com --password 'secret'
#   DEFAULT_USER_PASSWORD=secret ./scripts/db/seed-dev-user.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [ ! -d ".venv" ]; then
  echo "Error: .venv not found. Create and activate the virtual environment first." >&2
  exit 1
fi

if [ -z "${VIRTUAL_ENV:-}" ]; then
  source .venv/bin/activate
fi

export PYTHONPATH="${PROJECT_ROOT}"
exec python scripts/db/seed-dev-user.py "$@"
