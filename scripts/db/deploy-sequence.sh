#!/usr/bin/env bash
#
# The deploy sequence: everything that happens to a database before a new image serves traffic.
#
#   1. alembic upgrade head              the schema, to the migrations this checkout ships
#   2. python -m scripts.db.seed_rbac    the RBAC catalogue, synced from the module manifests
#   3. ... seed_rbac --check             nothing drifted: exits non-zero with a diff if it did
#
# One definition, run in two places, so "what a deploy does" cannot mean two things:
#   - CI (.github/workflows/ci.yml, Issue 9) runs it on the PostGIS service container of every PR,
#     so a migration or seed that fails does so on a pull request, not during a release;
#   - the deploy (Issue 11) runs it inside the new image, before that image takes traffic.
# tests/unit/platform/test_workflow_guardrails.py fails if either stops calling this script.
#
# Reads DATABASE_URL from the environment (or .env), exactly as the app does. Every step is
# idempotent, so running it twice against the same database is safe.
#
# Usage: ./scripts/db/deploy-sequence.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> python -m scripts.db.seed_rbac"
python -m scripts.db.seed_rbac

echo "==> python -m scripts.db.seed_rbac --check"
python -m scripts.db.seed_rbac --check
