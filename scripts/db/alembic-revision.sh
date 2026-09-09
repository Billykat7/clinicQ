#!/usr/bin/env bash
set -euo pipefail

# Create a new Alembic revision.
# Usage:
#   ./scripts/database/alembic-revision.sh "add users table"

usage() {
  cat <<'EOF'
Create a new (empty) Alembic revision file under alembic/versions/.

Usage:
  ./scripts/db/alembic-revision.sh "migration message"

Development only — creating a revision is not part of a deploy. Write the
upgrade() AND a correct downgrade(), keep every statement schema-qualified to
`clinicq`, and split incompatible column changes into expand/contract steps.
See docs/CICD/MIGRATION-RUNBOOK.md before applying it.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [ "$#" -lt 1 ]; then
  usage >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

MESSAGE="$1"

exec alembic -c alembic.ini revision -m "${MESSAGE}"
