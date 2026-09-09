#!/usr/bin/env python3
"""Regenerate the golden RBAC decision snapshot (Issue #177, M29).

The **only** thing that writes ``tests/snapshots/rbac_decisions.txt``. Regeneration is a deliberate
act, run by a person from ``make rbac-snapshot``, and the resulting diff is reviewed in the PR that
caused it. CI re-derives and diffs but never writes: a guard that fixes itself is a rubber stamp,
and the whole point of this artifact is that a moved decision has to be *looked at* by someone.

All the derivation lives in :mod:`src.core.rbac_snapshot`, imported here and by the CI test, so the
two can never disagree about what the snapshot should contain.

Usage:
    make rbac-snapshot                       # rewrite the golden file
    scripts/generate_rbac_snapshot.py        # the same thing
    scripts/generate_rbac_snapshot.py --check  # exit 1 with a diff if it is stale (no write)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.rbac_snapshot import (  # noqa: E402 - after sys.path bootstrap
    SNAPSHOT_PATH,
    build_snapshot,
    diff_snapshots,
    read_snapshot,
    write_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    """Write the snapshot, or (with ``--check``) report how the live decisions differ from it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 and print the moved decisions if the file is stale.",
    )
    args = parser.parse_args(argv)

    target = REPO_ROOT / SNAPSHOT_PATH
    derived = build_snapshot()
    committed = read_snapshot(target)

    if not args.check:
        write_snapshot(derived, target)
        decisions = sum(
            1 for line in derived.splitlines() if line and not line.startswith("#")
        )
        print(f"Wrote {SNAPSHOT_PATH} ({decisions} decisions).")
        return 0

    changes = diff_snapshots(committed, derived)
    if not changes:
        print(f"{SNAPSHOT_PATH} is up to date.")
        return 0
    print(f"{len(changes)} decision(s) have moved since the snapshot was taken:\n")
    for change in changes:
        print(f"  {change}")
    print(
        "\nIf these changes are intended, run `make rbac-snapshot` and review the diff in "
        "the PR that causes them."
    )
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
