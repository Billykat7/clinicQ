#!/usr/bin/env bash
#
# Run the same checks as GitHub Actions CI locally (quality, tests, coverage, Docker build, Trivy).
# Optionally run a Docker Compose smoke test (up, health check, down).
# Use before pushing to catch failures early and preserve your monthly Actions quota.
#
# Stages run in order and the script stops at the first one that fails, naming it (Issue 7):
#   1  quality      ruff check, ruff format --check, mypy src/, the ruff pin
#   1b pip-audit    dependency CVEs (warn-only)
#   1c secrets      gitleaks over the whole history (fails)
#   2  tests        pytest, all markers, in parallel
#   2b coverage     the floor in [tool.coverage.report] fail_under (pyproject.toml), 75% of src/
#   3  docker       image build (+ Trivy, warn-only)
#   4  compose      the smoke test, with --compose
# The database-layer tests run in stage 2 when TEST_DATABASE_URL names a server; see CONTRIBUTING.md.
#
# Usage: ./scripts/ci-local.sh [--install] [--fix] [--no-docker] [--compose]
#                              [--skip-pip-audit] [--skip-trivy] [--skip-secret-scan]
# From project root: scripts/ci-local.sh
#
# Options:
#   --install         pip install -r requirements.txt (and pip-audit) before running checks
#   --fix             run ruff check --fix and ruff format (fix in place), then run checks
#   --no-docker       skip Docker build and Trivy (run only quality + tests)
#   --compose         after Docker/Trivy, bring the full compose stack up in its own project,
#                     hit /health, check PostGIS and Redis, then tear it down (smoke test)
#   --skip-pip-audit  skip pip-audit (otherwise warn-only; never fails the script)
#   --skip-trivy      skip Trivy (otherwise warn-only; never fails the script)
#   --skip-secret-scan  skip gitleaks (otherwise FAILS the script on a finding)
#

set -e

# Colors for output (same style as btkhomes/scripts/ci)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

INSTALL=false
FIX=false
NO_DOCKER=false
COMPOSE=false
SKIP_PIP_AUDIT=false
SKIP_SECRET_SCAN=false
SKIP_TRIVY=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --install)
      INSTALL=true
      shift
      ;;
    --fix)
      FIX=true
      shift
      ;;
    --no-docker)
      NO_DOCKER=true
      shift
      ;;
    --compose)
      COMPOSE=true
      shift
      ;;
    --skip-pip-audit)
      SKIP_PIP_AUDIT=true
      shift
      ;;
    --skip-secret-scan)
      SKIP_SECRET_SCAN=true
      shift
      ;;
    --skip-trivy)
      SKIP_TRIVY=true
      shift
      ;;
    *)
      echo "Usage: $0 [--install] [--fix] [--no-docker] [--compose] [--skip-pip-audit] [--skip-trivy] [--skip-secret-scan]"
      echo "  --install         pip install -r requirements.txt (and pip-audit) before running checks"
      echo "  --fix             ruff check --fix and ruff format (fix in place), then run checks"
      echo "  --no-docker       skip Docker build and Trivy (quality + tests only)"
      echo "  --compose         after Docker, compose up → /health → PostGIS + Redis → down (smoke test)"
      echo "  --skip-pip-audit  skip pip-audit (otherwise warn-only; never fails the script)"
      echo "  --skip-trivy      skip Trivy (otherwise warn-only; never fails the script)"
      echo "  --skip-secret-scan  skip gitleaks (otherwise FAILS the script on a finding)"
      exit 1
      ;;
  esac
done

# Activate venv if present (matches .cursor/rules: activate-venv-before-commands)
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
elif [[ -f venv/bin/activate ]]; then
  source venv/bin/activate
fi

run() {
  echo "==> $*"
  "$@"
}

# Stage bookkeeping: the name of the stage running now, and how long each finished one took. A
# failure anywhere (an explicit `exit 1` or `set -e`) reaches the EXIT trap, which names the stage.
STAGE="setup"
STAGE_STARTED=$SECONDS
SCRIPT_STARTED=$SECONDS
STAGE_TIMES=()

stage() {
  STAGE="$1"
  STAGE_STARTED=$SECONDS
}

stage_done() {
  STAGE_TIMES+=("$(printf '%-12s %4ss' "$STAGE" "$((SECONDS - STAGE_STARTED))")")
}

on_exit() {
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo ""
    echo -e "${RED}✗ ci-local.sh failed at stage: ${STAGE} (after $((SECONDS - SCRIPT_STARTED))s, exit ${code})${NC}"
  fi
}
trap on_exit EXIT

run_pip_audit_with_retry() {
  local attempts=3
  local sleep_seconds=5
  local attempt=1
  local output=""
  local exit_code=0

  while [[ $attempt -le $attempts ]]; do
    echo "==> pip-audit attempt ${attempt}/${attempts}"
    set +e
    output="$(pip-audit -r requirements.txt --desc "${PIP_AUDIT_IGNORE_PYGMENTS[@]}" 2>&1)"
    exit_code=$?
    set -e

    if [[ $exit_code -eq 0 ]]; then
      echo "$output"
      return 0
    fi

    echo "$output"
    # A report of vulnerabilities is a result, not a transient failure: asking again returns the
    # same list three times over (Issue 7 measured a minute lost that way). Retry only when
    # pip-audit produced no result at all.
    if grep -qE "Found [0-9]+ known vulnerabilit" <<<"$output"; then
      break
    fi
    if [[ $attempt -lt $attempts ]]; then
      echo -e "${YELLOW}⚠️  pip-audit failed (likely transient network/PyPI issue); retrying in ${sleep_seconds}s...${NC}"
      sleep "$sleep_seconds"
    fi
    attempt=$((attempt + 1))
  done

  echo -e "${YELLOW}⚠️  pip-audit reported issues (or failed) — warning only, not failing CI.${NC}"
  return "$exit_code"
}

# Scan warnings (non-blocking) reported in the summary.
PIP_AUDIT_WARNED=false
TRIVY_WARNED=false

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                   Running CI Checks Locally (clinicq)                      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

if $INSTALL; then
  echo -e "${YELLOW}Installing dependencies...${NC}"
  run pip install -q -r requirements.txt
  if ! $SKIP_PIP_AUDIT; then
    run pip install -q pip-audit
  fi
  echo ""
fi

if $FIX; then
  echo -e "${YELLOW}--- Ruff fix (in place) ---${NC}"
  run ruff check . --fix
  run ruff format .
  echo ""
fi

# ─── STEP 1: Code quality (ruff, mypy) ───
stage "quality"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 1: Code quality (ruff, mypy)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Bare `ruff check` / `ruff format` — rules come from [tool.ruff] in pyproject.toml,
# the same section the pre-commit hooks and CI read. `.` matches CI's scope.
parallel_quality() {
  set -e
  ruff check . & pid_ruff_check=$!
  ruff format --check . & pid_ruff_fmt=$!
  mypy src/ & pid_mypy=$!
  ec=0
  wait "$pid_ruff_check" || ec=1
  wait "$pid_ruff_fmt" || ec=1
  wait "$pid_mypy" || ec=1
  "$ROOT_DIR/scripts/check-ruff-pin.sh" || ec=1
  return "$ec"
}
if parallel_quality; then
  stage_done
  echo ""
  echo -e "${GREEN}✅ Code quality passed${NC}"
  echo ""
else
  echo ""
  echo -e "${RED}❌ Code quality failed${NC}"
  echo ""
  echo -e "${YELLOW}💡 Fix issues above, or run with --fix to auto-fix ruff.${NC}"
  echo -e "${YELLOW}   This saves GitHub Actions quota by catching issues locally.${NC}"
  exit 1
fi

# ─── STEP 1b: pip-audit (warn-only; never fails the script) ───
# pip-audit: Pygments CVE-2026-4539 — no fixed release on PyPI yet (transitive via pytest/rich); local-access ReDoS.
#
# Since Issue 180 (M30) this is no longer local-only SCA: .github/workflows/vulnerability-scan.yml
# runs the same audit weekly and on any PR touching requirements.txt, where it **blocks**. This run
# stays warn-only on purpose — it is a pre-push convenience, and a transient PyPI failure must not
# stop a developer pushing — but the ignore list below is kept byte-identical to the workflow's
# PIP_AUDIT_IGNORE, and tests/unit/platform/test_scanning_config.py fails the build if the two
# drift. A CVE ignored in one place and not the other is how "but it passed locally" starts.
PIP_AUDIT_IGNORE_PYGMENTS=(--ignore-vuln CVE-2026-4539)
stage "pip-audit"
if $SKIP_PIP_AUDIT; then
  echo -e "${YELLOW}⏭️  Skipping pip-audit (--skip-pip-audit)${NC}"
  echo ""
else
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}STEP 1b: Dependency scan (pip-audit, warn-only)${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
  if command -v pip-audit &>/dev/null || pip install -q pip-audit; then
    if run_pip_audit_with_retry; then
      echo ""
      echo -e "${GREEN}✅ pip-audit passed${NC}"
      echo ""
    else
      PIP_AUDIT_WARNED=true
      echo ""
      echo -e "${YELLOW}⚠️  pip-audit findings/failures are warnings only; continuing.${NC}"
      echo ""
    fi
  else
    PIP_AUDIT_WARNED=true
    echo -e "${YELLOW}⚠️  Could not install/run pip-audit; skipping (warning only).${NC}"
    echo ""
  fi
fi

# ─── STEP 1c: secret scan (gitleaks; FAILS the script) ───
# Unlike pip-audit above this is not warn-only, and it matches the workflow exactly: a committed
# credential is actionable immediately and always, by rotating it, so there is no "unfixable
# finding" case to be tolerant of. Catching it before the push is the whole point — afterwards the
# secret is in history and rotation stops being optional.
#
# Skipped with a notice when gitleaks is not installed: a missing local tool must not block a push
# when CI runs the same scan anyway (`brew install gitleaks`).
stage_done
stage "secrets"
if $SKIP_SECRET_SCAN; then
  echo -e "${YELLOW}⏭️  Skipping secret scan (--skip-secret-scan)${NC}"
  echo ""
elif ! command -v gitleaks &>/dev/null; then
  echo -e "${YELLOW}⚠️  gitleaks not installed; skipping the secret scan (CI still runs it).${NC}"
  echo -e "${YELLOW}   Install it with: brew install gitleaks${NC}"
  echo ""
else
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}STEP 1c: Secret scan (gitleaks)${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
  if run gitleaks detect --source . --config .gitleaks.toml --baseline-path .gitleaks-baseline.json --redact --no-banner; then
    echo ""
    echo -e "${GREEN}✅ No committed secrets${NC}"
    echo ""
  else
    echo ""
    echo -e "${RED}❌ gitleaks found a committed secret${NC}"
    echo ""
    echo -e "${YELLOW}💡 Rotate it first — it is already in git history — then remove it from the tree.${NC}"
    echo -e "${YELLOW}   A reviewed test fixture belongs in .gitleaks-baseline.json, never in an allowlist path.${NC}"
    exit 1
  fi
fi

# ─── STEP 2: Tests, with coverage collected ───
stage_done
stage "tests"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 2: Tests (pytest, coverage collected)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# The floor is checked in its own stage below, so a red run says whether a test broke or coverage
# dropped; here coverage is only collected (--cov-fail-under=0, no report).
if run pytest tests/ -q -n auto --dist loadscope --cov --cov-report= --cov-fail-under=0; then
  stage_done
  echo ""
  echo -e "${GREEN}✅ Tests passed${NC}"
  echo ""
else
  echo ""
  echo -e "${RED}❌ Tests failed${NC}"
  echo ""
  echo -e "${YELLOW}💡 Fix failing tests before pushing.${NC}"
  exit 1
fi

# ─── STEP 2b: Coverage floor ───
stage "coverage"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 2b: Coverage (fail_under in pyproject.toml)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# `coverage report` reads [tool.coverage.report] and exits 2 below fail_under. The table lists only
# files that are not fully covered (skip_covered), with their missing lines.
if run coverage report; then
  stage_done
  echo ""
  echo -e "${GREEN}✅ Coverage at or above the floor${NC}"
  echo ""
else
  echo ""
  echo -e "${RED}❌ Coverage is below the floor in pyproject.toml ([tool.coverage.report] fail_under)${NC}"
  echo ""
  echo -e "${YELLOW}💡 Test the code you added; do not lower the floor to get a change through.${NC}"
  exit 1
fi

# ─── STEP 3: Docker build + Trivy (Trivy warn-only) ───
stage "docker"
if ! $NO_DOCKER; then
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}STEP 3: Docker build + Trivy scan (Trivy warn-only)${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""

  # --pull: fresh python:3.14-slim + apt indexes so OpenSSL/security updates are not masked by cache.
  # Dockerfile lives under infra/docker/; build context is repo root.
  if run docker build --pull -f infra/docker/Dockerfile -t clinicq-app:local . ; then
    stage_done
    if $SKIP_TRIVY; then
      echo -e "${YELLOW}⏭️  Skipping Trivy (--skip-trivy)${NC}"
      echo ""
      echo -e "${GREEN}✅ Docker build passed (Trivy skipped)${NC}"
      echo ""
    elif command -v trivy &>/dev/null; then
      # Ignore unfixed OS CVEs (no fixed package in Debian yet); warn on fixable HIGH/CRITICAL.
      set +e
      run trivy image --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed clinicq-app:local
      trivy_ec=$?
      set -e
      if [[ $trivy_ec -eq 0 ]]; then
        echo ""
        echo -e "${GREEN}✅ Docker build and Trivy scan passed${NC}"
        echo ""
      else
        TRIVY_WARNED=true
        echo ""
        echo -e "${YELLOW}⚠️  Trivy found CRITICAL/HIGH issues — warning only, not failing CI.${NC}"
        echo ""
        echo -e "${GREEN}✅ Docker build passed (Trivy warned)${NC}"
        echo ""
      fi
    else
      echo -e "${YELLOW}⚠️  Trivy not installed; skipping scan. Install: brew install trivy${NC}"
      echo ""
      echo -e "${GREEN}✅ Docker build passed (Trivy skipped)${NC}"
      echo ""
    fi
  else
    echo ""
    echo -e "${RED}❌ Docker build failed${NC}"
    exit 1
  fi
fi

# ─── STEP 4 (optional): Docker Compose smoke test ───
stage "compose"
if $COMPOSE; then
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}STEP 4: Docker Compose smoke test (up → /health → PostGIS + Redis → down)${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""

  COMPOSE_FILE="infra/docker/docker-compose.yml"
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo -e "${RED}❌ ${COMPOSE_FILE} not found${NC}"
    exit 1
  fi

  # Compose v2 only: the stack uses `include`, an optional env_file and `--wait`, none of which the
  # legacy docker-compose v1 binary understands.
  if ! docker compose version &>/dev/null; then
    echo -e "${RED}❌ docker compose (v2) not found${NC}"
    exit 1
  fi

  # Its own project name and host ports, so the smoke test never touches the developer's stack:
  # `down -v` on the dev project (`clinicq`) would wipe the local database, and a running dev stack
  # holds the default ports. Override with SMOKE_HTTP_PORT / SMOKE_DB_PORT / SMOKE_REDIS_PORT.
  SMOKE_HTTP_PORT="${SMOKE_HTTP_PORT:-18000}"
  SMOKE_COMPOSE=(env HTTP_PORT="$SMOKE_HTTP_PORT" DB_PORT="${SMOKE_DB_PORT:-15432}"
    REDIS_PORT="${SMOKE_REDIS_PORT:-16379}"
    docker compose -p clinicq-smoke -f "$COMPOSE_FILE" --project-directory infra/docker)

  smoke_fail() {
    echo -e "${RED}❌ $1${NC}"
    "${SMOKE_COMPOSE[@]}" logs --tail=30 || true
    "${SMOKE_COMPOSE[@]}" down -v 2>/dev/null || true
    exit 1
  }

  echo "==> docker compose -p clinicq-smoke down -v (cleanup)"
  "${SMOKE_COMPOSE[@]}" down -v 2>/dev/null || true
  # --wait returns once db and redis are healthy and the app's liveness probe passes.
  echo "==> docker compose -p clinicq-smoke up -d --build --wait"
  if ! "${SMOKE_COMPOSE[@]}" up -d --build --wait --wait-timeout 180; then
    smoke_fail "Docker Compose up failed (a service never became healthy)"
  fi

  # Liveness only, through nginx: /health/live is dependency-free, so this smoke passes without
  # applying migrations (readiness /health/ready would 503 until `alembic upgrade head`).
  echo "==> Waiting for app /health/live on :${SMOKE_HTTP_PORT} (max 60s)..."
  MAX_WAIT=60
  ELAPSED=0
  HEALTH_OK=false
  while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -sf "http://localhost:${SMOKE_HTTP_PORT}/health/live" >/dev/null 2>&1; then
      HEALTH_OK=true
      break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
  done
  if ! $HEALTH_OK; then
    smoke_fail "/health/live not reachable within ${MAX_WAIT}s"
  fi
  echo -e "${GREEN}✅ /health/live returned 200${NC}"

  # What the dev stack promises (Issue 2): PostGIS answers in the compose database, on the
  # PostgreSQL version decision 4 chose, and Redis answers PING.
  echo "==> PostgreSQL and PostGIS versions in the compose database"
  # shellcheck disable=SC2016  # $POSTGRES_USER / $POSTGRES_DB expand inside the container
  if ! "${SMOKE_COMPOSE[@]}" exec -T db sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "SHOW server_version" -c "SELECT postgis_version()"'; then
    smoke_fail "SELECT postgis_version() failed in the compose database"
  fi
  echo "==> Redis PING"
  if ! "${SMOKE_COMPOSE[@]}" exec -T redis redis-cli ping | grep -q PONG; then
    smoke_fail "Redis did not answer PING"
  fi
  echo -e "${GREEN}✅ PostGIS and Redis answered${NC}"

  echo "==> docker compose -p clinicq-smoke down -v"
  "${SMOKE_COMPOSE[@]}" down -v 2>/dev/null || true
  stage_done
  echo ""
  echo -e "${GREEN}✅ Docker Compose smoke test passed${NC}"
  echo ""
fi

# ─── Final summary ───
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                            Final Summary                                     ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${GREEN}✅ All CI checks passed locally in $((SECONDS - SCRIPT_STARTED))s${NC}"
for line in "${STAGE_TIMES[@]}"; do
  echo "   ${line}"
done
if $PIP_AUDIT_WARNED || $TRIVY_WARNED; then
  echo ""
  if $PIP_AUDIT_WARNED; then
    echo -e "${YELLOW}⚠️  pip-audit had warnings (non-blocking).${NC}"
  fi
  if $TRIVY_WARNED; then
    echo -e "${YELLOW}⚠️  Trivy had warnings (non-blocking).${NC}"
  fi
fi
echo ""
echo -e "${GREEN}💡 You can push your changes. GitHub Actions will still run, but you've verified everything locally.${NC}"
exit 0
