#!/usr/bin/env python3
"""Apply the branch rulesets in .github/rulesets/*.json to the repository (Issue 13).

The JSON files are the source of truth for what protects `main`; this script makes GitHub match
them. Each file is one ruleset, matched to GitHub's by its `name`: an existing one is updated in
place (so its id and its insights history survive), a missing one is created. Rulesets on GitHub
that no file names are reported and left alone; delete them in the settings page if they are
really unwanted.

`gh` must be installed and authenticated with admin rights on the repository.

Usage:
    scripts/gh_sync_rulesets.py              # create or update every ruleset
    scripts/gh_sync_rulesets.py --dry-run    # say what would change, change nothing
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RULESETS_DIR = REPO_ROOT / ".github" / "rulesets"

#: The parts of a ruleset a file defines; GitHub adds ids, links and timestamps around them.
COMPARED_KEYS = ("target", "enforcement", "conditions", "bypass_actors", "rules")


def gh_api(*args: str, body: dict[str, Any] | None = None) -> Any:
    """Call `gh api`, sending ``body`` as JSON when given, and return the parsed response."""
    command = ["gh", "api", *args]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"gh api {' '.join(args)} failed:\n{result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def load_desired() -> list[dict[str, Any]]:
    """Every ruleset file, parsed; each must have a unique name."""
    rulesets = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(RULESETS_DIR.glob("*.json"))
    ]
    names = [ruleset["name"] for ruleset in rulesets]
    if len(names) != len(set(names)):
        raise SystemExit(f"two ruleset files share a name: {names}")
    return rulesets


def _canonical(value: Any) -> Any:
    """A form of a ruleset part that compares equal whatever the key or list order."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((_canonical(item) for item in value), key=json.dumps)
    return value


def differences(desired: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """The top-level parts of a ruleset that GitHub has differently from the file."""
    return [
        key
        for key in COMPARED_KEYS
        if _canonical(desired.get(key)) != _canonical(current.get(key))
    ]


def main() -> int:
    """Create or update each ruleset file's ruleset on the current repository."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="change nothing")
    args = parser.parse_args()

    repo = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    existing = {
        ruleset["name"]: ruleset["id"]
        for ruleset in gh_api(f"repos/{repo}/rulesets", "--paginate")
    }
    desired = load_desired()
    for ruleset in desired:
        name = ruleset["name"]
        if name not in existing:
            print(f"{'would create' if args.dry_run else 'create'}  {name}")
            if not args.dry_run:
                gh_api("-X", "POST", f"repos/{repo}/rulesets", body=ruleset)
            continue
        current = gh_api(f"repos/{repo}/rulesets/{existing[name]}")
        changed = differences(ruleset, current)
        if not changed:
            print(f"unchanged     {name} (id {existing[name]})")
            continue
        print(
            f"{'would update' if args.dry_run else 'update'}  {name} (id {existing[name]}): "
            + ", ".join(changed)
        )
        if not args.dry_run:
            gh_api("-X", "PUT", f"repos/{repo}/rulesets/{existing[name]}", body=ruleset)
    for name in sorted(set(existing) - {ruleset["name"] for ruleset in desired}):
        print(f"not in files  {name} (left alone)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
