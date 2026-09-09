#!/usr/bin/env bash
#
# Assert that the ruff version developers get from pre-commit is the one CI runs.
#
# CI installs requirements.txt (`ruff==X.Y.Z`); pre-commit builds its own isolated
# environment from the `rev:` of the astral-sh/ruff-pre-commit repo. Those are two
# places recording one version, so they drift — and when they do, a commit that
# passed the hook can still fail the `quality` job on a rule that changed between
# releases. This script makes the drift a failed commit instead of a failed pipeline.
#
# Run by the `ruff-pin` pre-commit hook whenever either file is staged, and by
# `scripts/ci-local.sh`. Deliberately POSIX shell + grep/sed only: no venv, no
# Python, so it also works from a GUI git client with a bare PATH.
#
# Usage: ./scripts/check-ruff-pin.sh
# Exit:  0 versions match · 1 mismatch or a version could not be read

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

REQUIREMENTS="$ROOT_DIR/requirements.txt"
PRE_COMMIT_CONFIG="$ROOT_DIR/.pre-commit-config.yaml"

# `ruff==0.15.5` → `0.15.5` (first match wins; extras/markers are not used here).
requirements_version="$(sed -n 's/^ruff==\([0-9][0-9A-Za-z.]*\).*$/\1/p' "$REQUIREMENTS" | head -n 1)"

# The `rev: v0.15.5` on the line following the ruff-pre-commit repo URL → `0.15.5`.
hook_version="$(
  grep -A1 'astral-sh/ruff-pre-commit' "$PRE_COMMIT_CONFIG" |
    sed -n 's/^[[:space:]]*rev:[[:space:]]*"\{0,1\}v\{0,1\}\([0-9][0-9A-Za-z.]*\)"\{0,1\}[[:space:]]*$/\1/p' |
    head -n 1
)"

if [ -z "$requirements_version" ]; then
  echo "check-ruff-pin: no 'ruff==<version>' pin found in requirements.txt" >&2
  exit 1
fi

if [ -z "$hook_version" ]; then
  echo "check-ruff-pin: no 'rev:' found under astral-sh/ruff-pre-commit in .pre-commit-config.yaml" >&2
  exit 1
fi

if [ "$requirements_version" != "$hook_version" ]; then
  cat >&2 <<EOF
check-ruff-pin: ruff versions disagree.

  requirements.txt        ruff==${requirements_version}   (what CI and ./scripts/ci-local.sh run)
  .pre-commit-config.yaml rev: v${hook_version}   (what 'pre-commit run' runs)

Set both to the same version, then re-stage. To take the requirements.txt pin:

  sed -i '' 's/rev: v${hook_version}/rev: v${requirements_version}/' .pre-commit-config.yaml
EOF
  exit 1
fi

echo "check-ruff-pin: ruff ${requirements_version} in requirements.txt and .pre-commit-config.yaml"
