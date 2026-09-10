#!/usr/bin/env python3
"""Sync GitHub issue/PR labels from docs/GITHUB/LABELS/labels.yml.

The YAML file is the single source of truth: a flat list of entries, each with a
`name`, a 6-char hex `color` (no '#'), and a `description`. This script upserts
every entry onto the repo through the `gh` CLI (create, or update colour and
description with `--force`). It is idempotent, so it is safe to re-run.

By default nothing is deleted. Pass `--prune` to also remove labels that exist on
GitHub but are absent from the YAML file — this detaches them from every issue and
PR, so it is opt-in and off by the `make gh-sync-labels` default.

`gh` must be installed and authenticated (`gh auth status`).

Usage:
    scripts/gh_sync_labels.py                # create/update all labels
    scripts/gh_sync_labels.py --dry-run      # show what would change
    scripts/gh_sync_labels.py --prune        # also delete labels not in the file
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELS_FILE = REPO_ROOT / "docs" / "GITHUB" / "LABELS" / "labels.yml"


def run(cmd: list[str], *, capture: bool = True) -> str:
    """Run a command, raising with a helpful message on failure."""
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        sys.stderr.write(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}\n"
        )
        raise SystemExit(result.returncode)
    return result.stdout.strip() if capture else ""


def ensure_gh() -> None:
    """Fail fast with a clear message if the `gh` CLI is missing or unauthenticated."""
    try:
        run(["gh", "auth", "status"])
    except FileNotFoundError:
        # `from None`: the traceback adds nothing to a one-line CLI prerequisite message.
        raise SystemExit(
            "`gh` CLI not found. Install it: https://cli.github.com"
        ) from None


def load_desired() -> list[dict[str, str]]:
    """Return the label definitions from the YAML file, validated and normalised."""
    if not LABELS_FILE.is_file():
        raise SystemExit(f"labels file not found: {LABELS_FILE}")
    raw: Any = yaml.safe_load(LABELS_FILE.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise SystemExit("labels.yml must be a top-level list of label entries")
    labels: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry:
            raise SystemExit(f"invalid label entry (needs a 'name'): {entry!r}")
        labels.append(
            {
                "name": str(entry["name"]),
                "color": str(entry.get("color", "")).lstrip("#").lower(),
                "description": str(entry.get("description", "")),
            }
        )
    return labels


def fetch_existing() -> dict[str, dict[str, str]]:
    """Return {label_name: {color, description}} for every label on the repo."""
    raw = run(
        ["gh", "label", "list", "--limit", "500", "--json", "name,color,description"]
    )
    return {
        row["name"]: {
            "color": str(row.get("color", "")).lstrip("#").lower(),
            "description": row.get("description", "") or "",
        }
        for row in json.loads(raw)
    }


def upsert(label: dict[str, str], existing: dict[str, dict[str, str]]) -> str:
    """Create the label, or update it in place with --force. Returns an action tag."""
    name, color, desc = label["name"], label["color"], label["description"]
    current = existing.get(name)
    if current is None:
        action = "create"
    elif current["color"] == color and current["description"] == desc:
        return "unchanged"
    else:
        action = "update"
    cmd = ["gh", "label", "create", name, "--force"]
    if color:
        cmd += ["--color", color]
    if desc:
        cmd += ["--description", desc]
    run(cmd)
    return action


def main() -> int:
    """Parse arguments and sync labels from the YAML file to GitHub."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print actions without changing labels"
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete labels on GitHub that are not in labels.yml (destructive)",
    )
    args = parser.parse_args()

    ensure_gh()
    desired = load_desired()
    existing = fetch_existing()
    desired_names = {label["name"] for label in desired}

    created = updated = unchanged = 0
    for label in desired:
        if args.dry_run:
            current = existing.get(label["name"])
            if current is None:
                verb = "would create"
                created += 1
            elif (
                current["color"] == label["color"]
                and current["description"] == label["description"]
            ):
                verb = "unchanged"
                unchanged += 1
            else:
                verb = "would update"
                updated += 1
            print(f"{verb:>13}  {label['name']}")
            continue

        match upsert(label, existing):
            case "create":
                print(f"      created  {label['name']}")
                created += 1
            case "update":
                print(f"      updated  {label['name']}")
                updated += 1
            case _:
                unchanged += 1

    pruned = 0
    stale = sorted(name for name in existing if name not in desired_names)
    for name in stale:
        if args.prune:
            if args.dry_run:
                print(f"  would delete  {name}")
            else:
                run(["gh", "label", "delete", name, "--yes"])
                print(f"      deleted  {name}")
            pruned += 1
        else:
            print(f"    not-in-file  {name}  (use --prune to delete)")

    print(
        f"\nDone. created={created} updated={updated} unchanged={unchanged} "
        f"stale={len(stale)} pruned={pruned}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
