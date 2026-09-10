#!/usr/bin/env bash
set -euo pipefail

# Roll back migrations.
# Usage:
#   ./scripts/database/alembic-downgrade.sh -1        # rollback a single step
#   ./scripts/database/alembic-downgrade.sh base      # rollback all the way to base
#   ./scripts/database/alembic-downgrade.sh <rev>     # rollback to a specific revision

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

usage() {
    cat <<'EOF'
Roll a migration back. Default is one step (-1).

Usage:
  ./scripts/db/alembic-downgrade.sh          # roll back a single step (-1)
  ./scripts/db/alembic-downgrade.sh base     # roll back all the way to base
  ./scripts/db/alembic-downgrade.sh <rev>    # roll back to a specific revision

WARNING: a downgrade is only a true rollback for a REVERSIBLE migration. Some
migrations are irreversible — their downgrade drops tables or deletes business
data (e.g. the append-only ledger). For those, recover from a backup instead of
downgrading. Read docs/CICD/MIGRATION-RUNBOOK.md (Irreversible migrations +
rollback decision tree) before running this against production.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

echo -e "${BLUE}🔄 Rolling Back Database Migrations${NC}"
echo ""

# Check if .venv exists
if [ ! -d ".venv" ]; then
    echo -e "${RED}❌ Error: .venv not found${NC}"
    echo -e "${YELLOW}   Please create and activate the virtual environment:${NC}"
    echo -e "${YELLOW}   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt${NC}"
    exit 1
fi

# Activate virtual environment if not already activated
if [ -z "${VIRTUAL_ENV:-}" ]; then
    echo -e "${YELLOW}⚠️  Activating .venv...${NC}"
    source .venv/bin/activate
fi

# Verify alembic is available
if ! command -v alembic &> /dev/null; then
    echo -e "${RED}❌ Error: alembic not found${NC}"
    echo -e "${YELLOW}   Please install dependencies: pip install -r requirements.txt${NC}"
    exit 1
fi

TARGET_REVISION="${1:--1}"

echo -e "${GREEN}✓ Downgrading database to: ${TARGET_REVISION}${NC}"
echo ""

# Set PYTHONPATH to project root so Python can find the 'app' module
export PYTHONPATH="${PROJECT_ROOT}"

exec alembic -c alembic.ini downgrade "${TARGET_REVISION}"
