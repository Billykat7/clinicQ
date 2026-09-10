#!/usr/bin/env bash
set -euo pipefail

# Run all pending migrations up to the latest (head).
# Usage:
#   ./scripts/db/alembic-upgrade.sh
#   ./scripts/db/alembic-upgrade.sh <target_revision>

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
Upgrade the database to the latest migration (head), or to a target revision.

Usage:
  ./scripts/db/alembic-upgrade.sh                 # upgrade to head
  ./scripts/db/alembic-upgrade.sh <target_rev>    # upgrade to a specific revision

Reads DATABASE_URL from the environment / .env — the same database the app uses —
and prints the target host/port/db/schema before running. CD never runs this; a
human applies migrations after reading the diff. Follow the full procedure in
docs/CICD/MIGRATION-RUNBOOK.md (back up → review with --sql → upgrade → verify).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

echo -e "${BLUE}🔄 Running Database Migrations${NC}"
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

TARGET_REVISION="${1:-head}"

# Set PYTHONPATH to project root so Python can find the 'src' module (same as btkhomes: app)
export PYTHONPATH="${PROJECT_ROOT}"

# Print DB host, port, name, and schema from settings (db_host/db_name synced from DATABASE_URL).
echo -e "${GREEN}✓${NC} $(python -c "
from sqlalchemy.engine.url import make_url
from src.core.config import get_settings
get_settings.cache_clear()
settings = get_settings()
u = make_url(settings.database_url)
host = (settings.db_host or '').strip() or (u.host or 'n/a')
db = (settings.db_name or '').strip() or (u.database or 'n/a')
port = u.port or 5432
schema = getattr(settings.db_schema, 'value', settings.db_schema)
print(f'DB HOST: {host}  |  PORT: {port}  |  DB: {db}  |  SCHEMA: {schema}')
")"
echo ""
echo -e "${GREEN}✓ Upgrading database to: ${TARGET_REVISION}${NC}"
echo ""

exec alembic -c alembic.ini upgrade "${TARGET_REVISION}"
