"""Portal RBAC manifest (Issue #152, M27).

Reproduces the ``portal`` resource tree (My portal / Owner portal / My jobs) exactly as the
``PermissionResource`` enum seeded it before Issue #154 deleted that enum — a parity pass. Portal pages live in ``src/web/routes.py``
(there is no ``src/modules/portal/`` package), hence this manifest file's home here.

Unlike every other manifest in this rollout, **no route in this codebase currently gates on a
``portal.*`` verb through a named ``rbac_deps`` dependency** to migrate away from: ``/portal``,
``/owner-portal`` and ``/jobs`` are gated only by authentication (``require_authenticated_html``)
plus server-side ownership resolution (``resolve_portal_tenant``/``resolve_portal_owner``/
``resolve_portal_vendor``) — confirmed by a full search of ``src/web/routes.py`` and every
``src/services/*_portal.py``/``*_view.py`` helper it calls. The icon that links to each portal is
gated by ``NavGateOverride``/``NavVisibility`` (``src/core/nav_visibility.py``), which already
resolves through the string-keyed catalog resolver, not a per-route ``Depends(require_x)``
dependency, so it needed no migration either. This manifest exists so the RBAC console's catalog
and grant editor can see/manage the ``portal`` tree (Issue #141) and so a future admin edit to a
portal's default nav gate has a manifest-synced row to update against — not because there is a
per-route enforcement dependency being replaced.
"""

from src.core.rbac_manifest import ModuleManifest, ResourceSpec

PORTAL_MANIFEST = ModuleManifest(
    key="portal",
    name="Portal",
    children=(
        ResourceSpec(key="tenant", name="Portal / My Portal"),
        ResourceSpec(key="owner", name="Portal / Owner Portal"),
        ResourceSpec(key="jobs", name="Portal / My Jobs"),
    ),
)
