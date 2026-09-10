#!/usr/bin/env bash
#
# Run mypy against the project's own Python 3.14 virtual environment, never
# whatever `mypy` a bare PATH happens to resolve to.
#
# The pre-commit `mypy` hook uses `language: system` so it type-checks with the
# project's real dependencies/stubs (see the comment in .pre-commit-config.yaml),
# but that means it trusts $PATH. A GUI git client, or a shell where `.venv`
# was never activated, can have a *different* mypy on PATH — e.g. a Homebrew
# install tied to a different Python version — which silently checks against
# the wrong interpreter instead of the pinned 3.14 (five-places pin, see
# pyproject.toml). This script finds the project venv explicitly so the hook
# runs Python 3.14 + the requirements.txt-pinned mypy regardless of shell state.
#
# Usage: ./scripts/run-mypy.sh [mypy args...]
# Exit:  whatever mypy exits; 1 if no mypy can be found at all.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/mypy" ]; then
  exec ".venv/bin/mypy" "$@"
fi

if [ -x "venv/bin/mypy" ]; then
  exec "venv/bin/mypy" "$@"
fi

if command -v mypy >/dev/null 2>&1; then
  echo "run-mypy: no .venv/venv found — falling back to '$(command -v mypy)' on PATH." >&2
  echo "run-mypy: create the project venv so checks run on the pinned Python 3.14 (see README)." >&2
  exec mypy "$@"
fi

echo "run-mypy: mypy not found in .venv, venv, or on PATH." >&2
echo "run-mypy: run 'python3.14 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt'." >&2
exit 1
