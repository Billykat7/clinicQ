#!/usr/bin/env python3
"""Remove the widgets example module (``make delete-example-module``).

The example exists to be read once and then deleted, so deleting it is one command rather than a
scavenger hunt. This removes the package, the model, the templates and every line that registers
them, then prints the two things it deliberately does **not** do:

* **drop the table** — that is a migration, and a migration is a reviewable artifact, not a side
  effect of a Makefile target;
* **edit the enums** — ``BoundedContext.WIDGETS`` and ``AuditEntityType.WIDGET`` may already be
  referenced by audit rows you have written, and removing an enum member that exists in the
  database turns a stored value into an error.

Idempotent: run it twice and the second run reports that there is nothing left to remove.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Files and directories that are entirely the example's.
PATHS = (
    "src/modules/widgets",
    "src/database/models/widget.py",
    "src/templates/admin/widgets.html",
)

#: Files that mention the example among other things: drop the lines that name it. Every one of
#: these is a plain list — an import, an ``include_router``, a tuple entry — so a line-level
#: filter is exactly right and nothing is left half-edited.
LINE_FILTERED = (
    "src/api/v1/router.py",
    "src/core/rbac_manifest_registry.py",
    "src/database/models/__init__.py",
)

#: Files where the example sits inside a block that a line filter would corrupt.
BY_HAND = (
    ("src/core/nav_registry.py", "the widgets NavDestination"),
    ("src/templates/partials/app_nav.html", "the widgets rail icon"),
    ("src/templates/dashboard.html", "the widgets card"),
    ("src/web/routes.py", "the /admin/widgets route"),
    ("src/commons/enums.py", "BoundedContext.WIDGETS and AuditEntityType.WIDGET"),
)


def main() -> int:
    """Remove what can be removed safely; report what is left. Returns the exit code."""
    removed: list[str] = []
    for rel in PATHS:
        path = ROOT / rel
        if not path.exists():
            continue
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        removed.append(rel)

    for rel in LINE_FILTERED:
        path = ROOT / rel
        if not path.exists():
            continue
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if "widget" not in line.lower()]
        if len(kept) != len(lines):
            path.write_text("".join(kept))
            removed.append(f"{rel} ({len(lines) - len(kept)} line(s))")

    if not removed:
        print("Nothing to remove — the widgets module is already gone.")
        return 0

    print("Removed:")
    for rel in removed:
        print(f"  {rel}")
    print("\nStill yours to do, in this order:")
    for rel, what in BY_HAND:
        print(f"  edit {rel:44} — {what}")
    print("  ./scripts/db/alembic-revision.sh 'drop the widget table'")
    print("  make migrate-up")
    print("  make lint && make test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
