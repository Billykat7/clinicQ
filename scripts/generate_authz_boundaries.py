#!/usr/bin/env python3
"""Generate the authorization-boundary (IDOR) table — every id-addressable route (Issue #178, M30).

The M17 threat model listed the ownership-gated resources **by hand**, and by M30 that list named
ten of the twenty modules in ``src/modules/``. A hand-written inventory of an attack surface does
not stay true; it only stays trusted. So this derives the surface the way the application actually
serves it — by walking the **router tree** of a live ``create_app()`` — and refuses to render a
table that does not cover every row it found.

Three columns are derived, never written down:

* **route** — method + full path, from the recursive walk (FastAPI's lazily-included routers are
  followed through ``_IncludedRouter``, so a module added to ``api_v1_router`` appears here the
  moment it is included).
* **RBAC requirement** — read back out of the route's own dependency closures. ``require``,
  ``require_action`` and ``require_management`` (``src.api.rbac_deps``) are factories; their
  ``resource_key``/``verb``/``scope`` live in the returned function's closure cells, so the
  requirement is recovered from the object the route will actually call. A route whose gate is
  called *inside* the handler (``ensure_permission_key(...)``) shows ``in-handler``.
* **new since M17** — set membership against ``docs/SECURITY/authz-baseline-m17.txt``, that
  commit's own derived route list.

One column is **declared**: the ownership/scope gate, in :data:`_OWNERSHIP_GATES` below — because
"which function binds this row to this caller" is a claim about enforcement that a reader must be
able to re-verify, not something to infer. It is declared as ordered ``(method, path)`` glob
patterns rather than one entry per route, and **every derived route must match one**: a route
added tomorrow with no declared gate fails this generator, and with it the drift test
(``tests/integration/security/test_authz_boundaries.py``). That is the anti-staleness mechanism —
the same shape as ``generate_surface_matrix.py``'s ``--check``.

Usage:
    scripts/generate_authz_boundaries.py            # rewrite the generated block in place
    scripts/generate_authz_boundaries.py --check    # exit 1 if the doc is stale (no write)
    scripts/generate_authz_boundaries.py --emit-baseline   # print the route list (baseline capture)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from fastapi.routing import APIRoute

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.main import create_app  # noqa: E402 - after sys.path bootstrap

BOUNDARY_DOC = REPO_ROOT / "docs" / "SECURITY" / "AUTHZ-BOUNDARIES.md"
BASELINE_FILE = REPO_ROOT / "docs" / "SECURITY" / "authz-baseline-m17.txt"
BEGIN_MARKER = "<!-- BEGIN GENERATED BOUNDARY TABLE -->"
END_MARKER = "<!-- END GENERATED BOUNDARY TABLE -->"

#: The declared ownership/scope gate per route, as ordered ``(method glob, path glob, gate)``.
#:
#: First match wins, so the specific patterns come before the general ones. ``gate`` names the
#: function that binds the addressed row to the caller — the second of the two gates §5 of the
#: threat model describes — or states in words why the route needs none. Every id-addressable
#: route the walk finds must match some pattern here or the generator fails.
_OWNERSHIP_GATES: tuple[tuple[str, str, str], ...] = (
    # --- no row to own: the caller's own session, or a signed token that *is* the authority ------
    (
        "*",
        "/api/v1/auth/avatars/{avatar_token}",
        "signed avatar token (`decode_avatar_token`) — no session read; the token is the gate",
    ),
    (
        "*",
        "/api/v1/auth/me/sessions/{token_id}",
        "self-scoped — `revoke_session_for_user(user_id=<caller>)`; another user's token id 404s",
    ),
    # --- properties ---------------------------------------------------------------------------
    (
        "*",
        "/api/v1/properties/{property_id}/units/{unit_id}/photos/{photo_id}",
        "`_load_photo_or_404` → `_load_unit_or_404` → `_load_scoped_property_or_404`",
    ),
    (
        "*",
        "/api/v1/properties/{property_id}/units/{unit_id}*",
        "`_load_unit_or_404` → `_load_scoped_property_or_404` (property-derived)",
    ),
    (
        "*",
        "/api/v1/properties/{property_id}*",
        "`_load_scoped_property_or_404` (`scoped_property_ids` + `property_in_scope`)",
    ),
    # --- applications ----------------------------------------------------------------------
    (
        "*",
        "/api/v1/applications/{application_id}/transition",
        "`_assert_application_in_scope` — property-derived through the application's unit",
    ),
    (
        "*",
        "/api/v1/tenants/applications/{application_id}/documents",
        "`_assert_application_in_scope` (tenants) — property-derived through the unit",
    ),
    # --- tenants / tenancies ------------------------------------------------------------------
    (
        "*",
        "/api/v1/tenants/me/*",
        "`_resolve_own_tenant` — the caller's own tenant row is the only reachable owner",
    ),
    (
        "*",
        "/api/v1/tenants/{tenant_id}/documents/{document_id}*",
        "`_load_scoped_tenant_or_404` + `_load_document_for_tenant_or_404` (document ⊂ tenant)",
    ),
    (
        "*",
        "/api/v1/tenants/{tenant_id}*",
        "`_load_scoped_tenant_or_404` (`scoped_tenant_ids` + `scoped_property_ids`)",
    ),
    (
        "*",
        "/api/v1/tenancies/{tenancy_id}*",
        "`_load_scoped_tenancy_or_404` → the tenancy's tenant (`_load_scoped_tenant_or_404`)",
    ),
    # --- leases, lease templates, payments ----------------------------------------------------
    (
        "*",
        "/api/v1/lease-templates/{name}*",
        "none — a template is business-wide content, not a per-principal row; verb only",
    ),
    (
        "*",
        "/api/v1/leases/units/{unit_id}/history",
        "`scoped_property_ids` + `scoped_tenant_ids` applied to the history query",
    ),
    (
        "GET",
        "/api/v1/leases/{lease_id}/balance",
        "`_authorize_balance_read` — `payments:read` **and** `_load_scoped_lease_or_404`, else the "
        "lease's own tenant",
    ),
    (
        "*",
        "/api/v1/leases/{lease_id}/ledger",
        "`_authorize_balance_read` (as above)",
    ),
    (
        "*",
        "/api/v1/leases/{lease_id}/record-payment-defaults",
        "`_authorize_balance_read` (as above)",
    ),
    (
        "*",
        "/api/v1/leases/{lease_id}/stripe/*",
        "`_authorize_balance_read` (as above)",
    ),
    (
        "*",
        "/api/v1/leases/{lease_id}/paystack/*",
        "`_authorize_balance_read` (as above)",
    ),
    (
        "*",
        "/api/v1/leases/{lease_id}*",
        "`_load_scoped_lease_or_404` (`scoped_tenant_ids` + `scoped_property_ids`)",
    ),
    (
        "*",
        "/api/v1/owners/{owner_id}/statement",
        "`_authorize_owner_statement_read` — `payment.statements:read` **and** every one of the "
        "owner's properties within `scoped_property_ids`, else the owner themselves",
    ),
    # --- inspections ---------------------------------------------------------------------------
    (
        "*",
        "/api/v1/inspections/checklist-templates/{property_type}",
        "none — a static checklist for a property *type*, not a stored row; verb only",
    ),
    (
        "*",
        "/api/v1/inspections/{inspection_id}*",
        "`_load_scoped_inspection_or_404` — property-derived through the inspected unit",
    ),
    # --- maintenance ---------------------------------------------------------------------------
    (
        "*",
        "/api/v1/maintenance/me/*",
        "`_resolve_own_tenant` + `_load_own_request_or_404` (`MaintenanceRequest.tenant_id`)",
    ),
    (
        "*",
        "/api/v1/maintenance/requests/{request_id}*",
        "`_load_scoped_request_or_404` (`scoped_tenant_ids` + property via unit)",
    ),
    (
        "*",
        "/api/v1/maintenance/work-orders/{work_order_id}*",
        "`_load_scoped_work_order_or_404` (`scoped_vendor_ids` + property via unit)",
    ),
    (
        "*",
        "/api/v1/maintenance/vendors/{vendor_id}*",
        "`_load_scoped_vendor_or_404` (`scoped_vendor_ids`)",
    ),
    # --- documents & e-signature ---------------------------------------------------------------
    (
        "*",
        "/api/v1/documents/{document_id}*",
        "`_authorize` → `_assert_owner_in_scope` — the owner row's property/tenant scope",
    ),
    (
        "*",
        "/api/v1/esign/envelopes/{envelope_id}*",
        "`_load_scoped_envelope_or_404` → `_ensure_lease_in_scope`",
    ),
    # --- communications ------------------------------------------------------------------------
    (
        "*",
        "/api/v1/messaging/drafts/{draft_id}*",
        "author-scoped — `drafts_service.*(db, draft_id, user_id=<caller>)`; another author's "
        "draft raises `MessageDraftNotFoundError` → 404",
    ),
    (
        "*",
        "/api/v1/messaging/threads/{thread_id}",
        "`_load_accessible_thread` (participant or manager) for read; delete/restore additionally "
        "require `_is_manager` on the thread's own kind",
    ),
    (
        "*",
        "/api/v1/messaging/threads/{thread_id}*",
        "`_load_accessible_thread` — participation, or the thread-kind manager gate",
    ),
    (
        "*",
        "/api/v1/alerts/drafts/{draft_id}*",
        "author-scoped — `drafts_service.*(db, draft_id, user_id=<caller>)` → 404",
    ),
    (
        "*",
        "/api/v1/alerts/{alert_id}/read",
        "recipient-scoped — `service.mark_read(db, alert_id, user_id=<caller>)`",
    ),
    (
        "*",
        "/api/v1/alerts/{alert_id}/unread",
        "recipient-scoped — `service.mark_unread(db, alert_id, user_id=<caller>)`",
    ),
    (
        "*",
        "/api/v1/alerts/{alert_id}*",
        "`_load_own_alert` — `Alert.author_id == <caller>`",
    ),
    (
        "*",
        "/api/v1/notifications/center/{item_id}*",
        "recipient-scoped — `center.mark_read/unread(db, <caller>.id, item_id)` → 404",
    ),
    (
        "*",
        "/api/v1/notifications/{notification_id}",
        "none — delivery status of one send, admin-only (`logs:read@business`); carries no "
        "principal's data beyond the address it was sent to",
    ),
    # --- audit / POPIA subject rights ----------------------------------------------------------
    (
        "*",
        "/api/v1/audit/subjects/{tenant_id}*",
        "`_require_tenant` — the subject must resolve to a live tenant; the surface is "
        "business-tier by grant (`logs:read@business` / `tenants:delete`)",
    ),
    # --- RBAC administration -------------------------------------------------------------------
    (
        "*",
        "/api/v1/admin/rbac/*",
        "none by design — the RBAC catalog is business-wide configuration with no per-principal "
        "owner; the gate is the `rbac`/`users` grant at `business` tier plus the audit trail",
    ),
    # --- public surfaces (no session, therefore no owner) --------------------------------------
    (
        "*",
        "/apply/{unit_id}",
        "none — the public application form for a *published* unit; anonymous by design, "
        "rate-limited per IP and per email, and it reads no principal's data",
    ),
    # --- server-rendered web consoles ----------------------------------------------------------
    (
        "*",
        "/portal*",
        "`_resolve_own_tenant` — the signed-in tenant's own rows only",
    ),
    (
        "*",
        "/owner/*",
        "owner-scoped — the console reads only properties the caller owns",
    ),
    (
        "*",
        "/jobs*",
        "vendor-scoped — `scoped_vendor_ids` on the caller's own vendor record",
    ),
    (
        "*",
        "/admin/*",
        "shell only — the page renders a nav-gated shell and every row it shows arrives from the "
        "`/api/v1` endpoint above, which carries the ownership gate",
    ),
    (
        "*",
        "/reports/*",
        "shell only — figures arrive from `/api/v1/reports/*`, which narrows by scope",
    ),
)


@dataclass(frozen=True)
class Row:
    """One id-addressable route and what gates it."""

    method: str
    path: str
    endpoint: str
    rbac: str
    ownership: str
    is_new: bool

    @property
    def key(self) -> str:
        """``METHOD /path`` — the identity used against the M17 baseline."""
        return f"{self.method} {self.path}"


def _walk(routes: list[object], prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Return ``(full path, route)`` for every ``APIRoute`` reachable from ``routes``.

    FastAPI includes routers lazily (``_IncludedRouter``), so ``app.routes`` alone shows only the
    handful of routes declared on the app object. Recursing through the include context is what
    makes this a walk of the *router tree* rather than of one list.
    """
    found: list[tuple[str, APIRoute]] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            found += _walk(
                included.routes, prefix + (route.include_context.prefix or "")
            )
        elif isinstance(route, APIRoute):
            found.append((prefix + route.path, route))
    return found


def _rbac_requirements(dependant: object) -> list[str]:
    """Recover the RBAC requirement(s) a route's dependency tree will enforce.

    ``src.api.rbac_deps``'s three factories all return a closure named ``dependency`` holding the
    call's ``resource_key``/``verb``/``action_key``/``scope`` in its free variables, so the
    requirement is read off the very object the route calls — no parsing, and no second list to
    keep in step with the call sites.
    """
    call = dependant.call  # type: ignore[attr-defined]
    out: list[str] = []
    code = getattr(call, "__code__", None)
    if (
        code is not None
        and getattr(call, "__name__", "") == "dependency"
        and call.__closure__
    ):
        cells = dict(
            zip(
                code.co_freevars,
                [c.cell_contents for c in call.__closure__],
                strict=True,
            )
        )
        resource = cells.get("resource_key")
        if resource is not None:
            action = cells.get("action_key")
            scope = cells.get("scope")
            requirement = f"`{resource}:{action or cells.get('verb')}`"
            if action is not None:
                requirement += " (named action)"
            elif scope is not None:
                requirement += f" @ `{scope.value}`"
            out.append(requirement)
    for sub in dependant.dependencies:  # type: ignore[attr-defined]
        out += _rbac_requirements(sub)
    return out


def _ownership_gate(method: str, path: str) -> str | None:
    """Return the declared ownership gate for a route, or ``None`` when none is declared."""
    for method_glob, path_glob, gate in _OWNERSHIP_GATES:
        if fnmatch(method, method_glob) and fnmatch(path, path_glob):
            return gate
    return None


def _baseline_keys() -> frozenset[str]:
    """Return the ``METHOD /path`` set captured at the M17 tip."""
    lines = BASELINE_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    )


def rows() -> list[Row]:
    """Return every id-addressable route in the live router tree, gates resolved.

    Raises:
        SystemExit: when a route matches no pattern in :data:`_OWNERSHIP_GATES` — a new
            id-addressable surface nobody has recorded a gate for.
    """
    baseline = _baseline_keys()
    collected: list[Row] = []
    undeclared: list[str] = []
    for path, route in _walk(create_app().routes):
        if "{" not in path:
            continue  # not id-addressable: nothing to guess
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            gate = _ownership_gate(method, path)
            if gate is None:
                undeclared.append(f"{method} {path}")
                continue
            collected.append(
                Row(
                    method=method,
                    path=path,
                    endpoint=f"{route.endpoint.__module__.rsplit('.', 1)[0].rsplit('.', 1)[-1]}"
                    f":{route.endpoint.__name__}",
                    rbac=" + ".join(_rbac_requirements(route.dependant))
                    or "in-handler",
                    ownership=gate,
                    is_new=f"{method} {path}" not in baseline,
                )
            )
    if undeclared:
        listing = "\n  ".join(sorted(undeclared))
        raise SystemExit(
            "Undeclared ownership gate for id-addressable route(s):\n  "
            f"{listing}\n\n"
            "Add a pattern to _OWNERSHIP_GATES in scripts/generate_authz_boundaries.py naming the "
            "check that binds the addressed row to the caller — and a test in "
            "tests/integration/security/ proving a non-owner holding the RBAC verb is refused."
        )
    collected.sort(key=lambda r: (r.path, r.method))
    return collected


def render_block() -> str:
    """Render the generated markdown block, markers included."""
    table = rows()
    new_count = sum(1 for r in table if r.is_new)
    lines = [
        BEGIN_MARKER,
        "",
        f"_{len(table)} id-addressable routes — {new_count} of them added since the M17 tip "
        f"(`{BASELINE_FILE.name}`). Derived from the live router tree by "
        "`scripts/generate_authz_boundaries.py`; do not edit by hand._",
        "",
        "| ★ | Route | Handler | RBAC requirement | Ownership / scope gate |",
        "|---|---|---|---|---|",
    ]
    for row in table:
        marker = "★" if row.is_new else ""
        lines.append(
            f"| {marker} | `{row.method} {row.path}` | `{row.endpoint}` | {row.rbac} | "
            f"{row.ownership} |"
        )
    lines += ["", "★ = added since the M17 baseline.", "", END_MARKER]
    return "\n".join(lines)


def _rewrite(doc: str, block: str) -> str:
    """Return ``doc`` with the region between the markers replaced by ``block``."""
    start = doc.index(BEGIN_MARKER)
    end = doc.index(END_MARKER) + len(END_MARKER)
    return doc[:start] + block + doc[end:]


def main() -> int:
    """Rewrite (or check, or capture the baseline for) the generated boundary table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the committed doc is stale"
    )
    parser.add_argument(
        "--emit-baseline",
        action="store_true",
        help="print this tree's id-addressable routes (used once, to capture a baseline)",
    )
    args = parser.parse_args()

    if args.emit_baseline:
        for path, route in sorted(_walk(create_app().routes), key=lambda pair: pair[0]):
            if "{" not in path:
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                print(f"{method} {path}")
        return 0

    block = render_block()
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    updated = _rewrite(doc, block)
    if args.check:
        if updated != doc:
            print(
                f"{BOUNDARY_DOC.relative_to(REPO_ROOT)} is stale — "
                "run scripts/generate_authz_boundaries.py",
                file=sys.stderr,
            )
            return 1
        return 0
    BOUNDARY_DOC.write_text(updated, encoding="utf-8")
    print(f"Wrote {BOUNDARY_DOC.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
