#!/usr/bin/env bash
#
# Deploy one ClinicQ image on this host, without ever leaving it with nothing that works (Issue 11).
#
# Runs on the target host, from its deploy directory (/opt/btk/clinicq, or /opt/btk/clinicq-staging),
# which holds this script, docker-compose.prod.yml, prune-old-app-images.sh and the app's .env.
# .github/workflows/deploy.yml copies those in and calls each step below over SSH, in this order:
#
#   deploy.sh preflight <image> <environment>  pull the image; check .env against its own rules
#   deploy.sh run-new <image> <command...>     one-off command in the new image: the migrations,
#                                              ./scripts/db/deploy-sequence.sh, before any traffic
#   deploy.sh candidate <image>                start it on a side port nothing routes to, smoke-test
#                                              it, remove it; a failure changes nothing that serves
#   deploy.sh swap <image>                     replace the serving container, smoke-test it live,
#                                              and roll back by itself if that fails
#   deploy.sh rollback                         put the previously serving image back, smoke-tested
#   deploy.sh smoke <port> <image>             the smoke check on its own
#   deploy.sh status                           what serves now, and what a rollback would restore
#
# The smoke check: /health reports the image's own version and commit (read from its labels),
# /health/ready answers 200 (database, migrations at head, Redis), and a real page and the asset
# that page needs answer 200.
#
# Environment:
#   DEPLOY_ENV       staging | production (default staging): picks the compose project, so the two
#                    can share a host without touching each other's containers
#   CANDIDATE_PORT   the side port (default APP_PORT + 1000, bound to 127.0.0.1)
#   SMOKE_TIMEOUT    seconds to wait for a container to answer (default 120)
#
# Every step is logged to .deploy/history.log; .deploy/current and .deploy/previous name the images.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$HERE/docker-compose.prod.yml"
ENV_FILE="$HERE/.env"
STATE="$HERE/.deploy"
DEPLOY_ENV="${DEPLOY_ENV:-staging}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-120}"

# The page and the asset the smoke check fetches: the landing page, and the htmx it loads (Issue 9
# found htmx missing from a clean checkout; a deploy must never ship that again).
SMOKE_PAGE="/"
SMOKE_ASSET="/static/vendor/htmx-2.0.0.min.js"

mkdir -p "$STATE"

die() {
  log "error: $*"
  exit 1
}

log() {
  printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "$DEPLOY_ENV" "$*" | tee -a "$STATE/history.log" >&2
}

case "$DEPLOY_ENV" in
  production) PROJECT="btk-clinicq" ;;
  staging) PROJECT="btk-clinicq-staging" ;;
  *) echo "DEPLOY_ENV must be staging or production, not '$DEPLOY_ENV'" >&2; exit 2 ;;
esac

# A value from .env, without sourcing it (it holds secrets, and may not be valid shell).
env_value() {
  local value
  value="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  value="${value%\"}"
  value="${value#\"}"
  echo "${value:-$2}"
}

APP_PORT="$(env_value APP_PORT 8011)"
CANDIDATE_PORT="${CANDIDATE_PORT:-$((APP_PORT + 1000))}"

compose() {
  local image="$1"
  shift
  IMAGE="$image" docker compose -f "$COMPOSE_FILE" --project-directory "$HERE" -p "$PROJECT" "$@"
}

label() {
  docker image inspect --format "{{index .Config.Labels \"org.opencontainers.image.$2\"}}" "$1"
}

ensure_image() {
  docker image inspect "$1" >/dev/null 2>&1 || docker pull --quiet "$1" >/dev/null
}

# The image the app container runs now, or nothing on a first deploy.
serving_image() {
  local id
  id="$(compose "unused:serving" ps -q app 2>/dev/null || true)"
  if [[ -n "$id" ]]; then
    docker inspect --format '{{.Config.Image}}' "$id"
  fi
}

smoke() {
  local port="$1" image="$2" version sha body deadline
  version="$(label "$image" version)"
  sha="$(label "$image" revision)"
  deadline=$((SECONDS + SMOKE_TIMEOUT))
  until body="$(curl -fsS "http://127.0.0.1:$port/health" 2>/dev/null)" &&
    [[ "$body" == *"\"version\":\"$version\""* && "$body" == *"\"git_sha\":\"$sha\""* ]]; do
    if ((SECONDS >= deadline)); then
      log "smoke: :$port never reported version $version ($sha) at /health"
      return 1
    fi
    sleep 2
  done
  local path
  for path in /health/ready "$SMOKE_PAGE" "$SMOKE_ASSET"; do
    if ! curl -fsS -o /dev/null "http://127.0.0.1:$port$path"; then
      log "smoke: :$port did not answer 200 at $path"
      return 1
    fi
  done
  log "smoke: :$port serves $version (${sha:0:7}); /health, /health/ready, $SMOKE_PAGE and $SMOKE_ASSET answer 200"
}

preflight() {
  local image="$1" environment="$2"
  [[ -f "$ENV_FILE" ]] || die "$ENV_FILE is missing"
  log "preflight: pulling $image"
  ensure_image "$image"
  # The new image judges the settings by its own rules (scripts/check_config.py, Issue 12). The file
  # goes in on stdin: it is mode 600 and the container does not run as its owner.
  log "preflight: checking .env as $environment with the new image's rules"
  docker run --rm -i --entrypoint python "$image" scripts/check_config.py - \
    --environment "$environment" <"$ENV_FILE" >&2
}

run_new() {
  local image="$1"
  shift
  ensure_image "$image"
  log "run-new: $* in $image"
  compose "$image" run --rm --no-deps -T app "$@"
}

candidate() {
  local image="$1" name="$PROJECT-candidate"
  ensure_image "$image"
  docker rm -f "$name" >/dev/null 2>&1 || true
  log "candidate: starting $image on 127.0.0.1:$CANDIDATE_PORT, which nothing routes to"
  compose "$image" run -d --no-deps --name "$name" -p "127.0.0.1:$CANDIDATE_PORT:8000" app >/dev/null
  if smoke "$CANDIDATE_PORT" "$image"; then
    docker rm -f "$name" >/dev/null
    log "candidate: passed; it never served traffic"
  else
    docker logs --tail 40 "$name" >&2 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
    log "candidate: failed; ${SERVING:-$(serving_image || true)} keeps serving, untouched"
    return 1
  fi
}

rollback() {
  local previous
  previous="$(cat "$STATE/previous" 2>/dev/null || true)"
  [[ -n "$previous" ]] || die "no previous image recorded in $STATE/previous"
  ensure_image "$previous"
  log "rollback: restoring $previous"
  compose "$previous" up -d --no-deps --wait --wait-timeout "$SMOKE_TIMEOUT" app
  smoke "$APP_PORT" "$previous"
  echo "$previous" >"$STATE/current"
  # One step back only: rolling back again would mean choosing a version, which is a deploy of that
  # tag with rollback set (deploy.yml), not a guess made here.
  rm -f "$STATE/previous"
  log "rollback: $previous serves again"
}

swap() {
  local image="$1" previous earlier
  previous="$(serving_image || true)"
  earlier="$(cat "$STATE/previous" 2>/dev/null || true)"
  if [[ -n "$previous" && "$previous" != "$image" ]]; then
    echo "$previous" >"$STATE/previous"
  fi
  log "swap: ${previous:-(nothing)} -> $image"
  if ! compose "$image" up -d --no-deps --wait --wait-timeout "$SMOKE_TIMEOUT" app ||
    ! smoke "$APP_PORT" "$image"; then
    log "swap: $image failed live"
    if [[ -n "$previous" ]]; then
      rollback
      # The failed image never counted: what was serving is current again, with its own previous.
      if [[ -n "$earlier" ]]; then
        echo "$earlier" >"$STATE/previous"
      fi
    fi
    return 1
  fi
  echo "$image" >"$STATE/current"
  log "deployed: $image"
  # Keep the image a rollback needs; remove the ones older than it (semver tags only).
  if [[ -n "$previous" && -x "$HERE/prune-old-app-images.sh" ]]; then
    "$HERE/prune-old-app-images.sh" "$previous" >&2 || log "prune: skipped ($?)"
  fi
}

status() {
  echo "environment: $DEPLOY_ENV (compose project $PROJECT, port $APP_PORT)"
  echo "serving:     $(serving_image || true)"
  echo "current:     $(cat "$STATE/current" 2>/dev/null || true)"
  echo "previous:    $(cat "$STATE/previous" 2>/dev/null || echo '(none: nothing to roll back to)')"
}

command="${1:-}"
shift || true
case "$command" in
  preflight) preflight "$@" ;;
  run-new) run_new "$@" ;;
  candidate) candidate "$@" ;;
  swap) swap "$@" ;;
  rollback) rollback ;;
  smoke) smoke "$@" ;;
  status) status ;;
  *)
    sed -n '3,20p' "${BASH_SOURCE[0]}" >&2
    exit 2
    ;;
esac
