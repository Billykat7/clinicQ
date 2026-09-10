"""Widgets RBAC manifest — how a module declares its own authorization footprint.

This is the whole self-registration story in one file. The module names its resources, the actions
on them, where they appear in the nav, and the grants it ships with. Nothing outside this directory
changes except one line in :mod:`src.core.rbac_manifest_registry` — no shared enum, no hand-written
seed migration. ``make seed-rbac`` pushes it into the ``resources`` / ``actions`` / ``permissions``
catalog idempotently, so re-running it after a redeploy inserts only what is new.

Three things worth copying into your own module:

* **The tree is the grant surface.** ``widgets.archive`` is a child, so a grant on ``widgets``
  cascades to it, while a role can be granted the archive alone. Nest a resource whenever you want
  it to be independently grantable.
* **``NavMeta`` declares the gate, not the layout.** The rail entry itself lives in
  :mod:`src.core.nav_registry`; what is synced from here is which resource+verb+tier the surface
  requires, so an admin can re-point it from the console without a deploy.
* **Ship your grants here.** :class:`~src.core.rbac_manifest.RoleGrant` rows are applied by the
  seed entrypoint, so a fresh database comes up with the module usable by the roles that should
  have it — rather than with a console nobody can open.
"""

from src.commons.enums import GrantScope, PermissionVerb
from src.core.rbac_manifest import ModuleManifest, NavMeta, ResourceSpec, RoleGrant

MANIFEST = ModuleManifest(
    key="widgets",
    name="Widgets",
    description="The example module's records.",
    nav=NavMeta(icon="list", href="/admin/widgets"),
    children=(
        ResourceSpec(
            key="archive",
            name="Widgets / Archive",
            description="Archived widgets, kept out of the working list.",
            nav=NavMeta(tab=True, scope=GrantScope.BUSINESS.value),
        ),
    ),
    grants=(
        RoleGrant(
            role="admin",
            resource="widgets",
            verb=PermissionVerb.DELETE.value,
            scope=GrantScope.BUSINESS.value,
        ),
    ),
)
