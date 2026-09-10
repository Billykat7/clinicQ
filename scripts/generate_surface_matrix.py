#!/usr/bin/env python3
"""Generate the RBAC **surface** matrix — what opens each page, tab and portal (Issue #170, M28).

``rbac-matrix.md`` answers *which role holds which verb on which resource*. It cannot answer the
question an operator actually asks when a console will not open: **what opens this page?** Until
this document, that took reading four files — ``nav_registry.py`` for the destination's declared
gate, each module's ``rbac_manifest.py`` for the sub-tab gates, ``nav_gate_overrides`` for whatever
an admin has re-pointed, and ``src/web/routes.py`` for the route that actually enforces it.

So this renders one row per surface: its key, the resource/verb/tier it ships with, and the route
that enforces it. Generated from the same registries the application reads at runtime, into a
delimited block of ``docs/architecture/rbac-surface-matrix.md``, with a drift test
(``tests/test_rbac_matrix.py``) failing if the committed block and this generator disagree.

The **live** gate (a row in ``nav_gate_overrides``) is deliberately not rendered: it is per-database
and an admin can change it without a deploy. What a document can honestly pin is the *shipped*
default, which is what the seed writes and what ``--check`` diffs against.

Usage:
    scripts/generate_surface_matrix.py            # rewrite the generated block in place
    scripts/generate_surface_matrix.py --check    # exit 1 if the doc is stale (no write)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.nav_registry import (  # noqa: E402 - after sys.path bootstrap
    NAV_DESTINATIONS,
)
from src.core.rbac_manifest import (  # noqa: E402 - after sys.path bootstrap
    iter_resources,
)
from src.core.rbac_manifest_registry import (  # noqa: E402 - after sys.path bootstrap
    ALL_MANIFESTS,
)

SURFACE_DOC = REPO_ROOT / "docs" / "architecture" / "rbac-surface-matrix.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED SURFACE MATRIX -->"
END_MARKER = "<!-- END GENERATED SURFACE MATRIX -->"

#: Which route enforces each destination's gate, recorded per row so a reader can re-verify it
#: rather than trust the table. Kept beside the generator (not in ``nav_registry``) because it is
#: documentation of where enforcement lives, not something the application reads.
_ENFORCING_ROUTE: dict[str, str] = {
    "reports": "`GET /reports/occupancy` (web) + `GET /api/v1/reports/occupancy`",
    "properties": "`GET /admin/properties` (web) + `GET /api/v1/properties`",
    "tenants": "`GET /admin/tenants` (web) + `GET /api/v1/tenants`",
    "leases": "`GET /admin/leases` (web) + `GET /api/v1/leases`",
    "invoices": "`GET /admin/invoices` (web) + `GET /api/v1/leases/{id}/payments`",
    "applications": "`GET /admin/applications` (web) + `GET /api/v1/applications`",
    "lease-templates": "`GET /admin/lease-templates` (web) + `GET /api/v1/lease-templates`",
    "maintenance": "`GET /admin/maintenance/requests` (web) + `GET /api/v1/maintenance/requests`",
    "inspections": "`GET /admin/inspections` (web) + `GET /api/v1/inspections`",
    "vendors": "`GET /admin/vendors` (web) + `GET /api/v1/maintenance/vendors`",
    "messages": "`GET /admin/messages/{tab}` (web) + `GET /api/v1/messaging/threads`",
    "notifications": "`GET /admin/notifications` (web) + `GET /api/v1/notifications`",
    "announcements": "`GET /admin/announcements/{tab}` (web) + `GET /api/v1/messaging/announcement-threads`",
    "alerts": "`GET /admin/alerts/{tab}` (web) + `GET /api/v1/alerts`",
    "portal": "`GET /portal` (web)",
    "owner": "`GET /owner` (web)",
    "jobs": "`GET /jobs` (web)",
    "rbac": "`GET /admin/rbac/{tab}` (web) + `GET /api/v1/admin/rbac/roles`",
    "logs": "`GET /admin/logs` (web) + `GET /api/v1/admin/logs`",
}


def _rows() -> list[tuple[str, str, str, str, str, str]]:
    """Return ``(kind, surface, resource, verb/action, tier, route)`` for every shipped surface."""
    rows: list[tuple[str, str, str, str, str, str]] = []
    for dest in NAV_DESTINATIONS:
        rows.append(
            (
                "destination",
                dest.key,
                dest.resource,
                dest.verb.value,
                dest.scope.value,
                _ENFORCING_ROUTE.get(dest.key, "—"),
            )
        )
    for manifest in ALL_MANIFESTS:
        for node in iter_resources(manifest):
            if node.nav is None:
                continue
            requirement = f"!{node.nav.action}" if node.nav.action else node.nav.verb
            rows.append(
                (
                    "manifest tab",
                    node.full_key,
                    node.full_key,
                    requirement,
                    node.nav.scope,
                    "its console's section route (`nav.can_surface`)",
                )
            )
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def render_block() -> str:
    """Render the generated markdown block, markers included."""
    rows = _rows()
    lines = [
        BEGIN_MARKER,
        "",
        f"_{len(rows)} surfaces — {sum(1 for r in rows if r[0] == 'destination')} nav destinations "
        f"and {sum(1 for r in rows if r[0] == 'manifest tab')} manifest-declared tabs. "
        "Generated by `scripts/generate_surface_matrix.py`; do not edit by hand._",
        "",
        "| Kind | Surface key | Shipped resource | Requirement | Tier | Enforced by |",
        "|---|---|---|---|---|---|",
    ]
    for kind, surface, resource, requirement, tier, route in rows:
        lines.append(
            f"| {kind} | `{surface}` | `{resource}` | `{requirement}` | `{tier}` | {route} |"
        )
    lines += ["", END_MARKER]
    return "\n".join(lines)


def _replace_block(doc: str, block: str) -> str:
    """Return ``doc`` with its delimited block replaced by ``block``."""
    start = doc.find(BEGIN_MARKER)
    end = doc.find(END_MARKER)
    if start == -1 or end == -1:
        raise SystemExit("generated-surface-matrix markers missing from the doc")
    return doc[:start] + block + doc[end + len(END_MARKER) :]


def main() -> int:
    """Rewrite the generated block, or check it for staleness."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the doc is stale; write nothing.",
    )
    args = parser.parse_args()

    doc = SURFACE_DOC.read_text(encoding="utf-8")
    block = render_block()
    start = doc.find(BEGIN_MARKER)
    end = doc.find(END_MARKER)
    committed = doc[start : end + len(END_MARKER)] if start != -1 and end != -1 else ""
    if args.check:
        if committed != block:
            print(
                f"{SURFACE_DOC.name} is stale — run scripts/generate_surface_matrix.py",
                file=sys.stderr,
            )
            return 1
        print(f"{SURFACE_DOC.name} is up to date")
        return 0
    SURFACE_DOC.write_text(_replace_block(doc, block), encoding="utf-8")
    print(f"Wrote generated surface matrix to {SURFACE_DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
