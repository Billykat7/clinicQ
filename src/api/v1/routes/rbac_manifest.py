"""Admin-shell RBAC manifests: the RBAC console, the Dashboard and the ``reports`` hub.

Every surface whose routes do **not** live under ``src/modules/`` declares its RBAC footprint here,
the same reasoning a module applies in its own ``rbac_manifest.py``:

* ``rbac`` — the access-control console (``rbac_admin.py``), a single ungrouped root.
* ``dashboard`` — the landing surface every signed-in user reaches. It has no route dependency of
  its own; the manifest exists so the resource the catalog carries has a declared home.
* ``reports`` — the **admin-shell hub**, not a reporting feature. Its two children are the log
  viewer (``admin.py``) and the user directory (``rbac_admin.py``). It carries no grant of its own,
  so nothing inherits *through* it; the parent links are what make ``logs`` and ``users`` sit under
  a heading in the console rather than floating at the catalog root.

  A user-facing reporting feature belongs in a module of its own, rooted at its own key —
  deliberately not this one, because a grant on this hub would inherit down into the log viewer and
  the user directory.

Since Issue #154 the registered manifests are the catalog's only source of truth, so every resource
key that ships is declared by one of them. ``sync_module_manifest`` only ever *sets* a parent, never
clears one, so re-declaring a parent here is a no-op against an existing database.
"""

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.core.rbac_manifest import ModuleManifest, ResourceSpec, RoleGrant

RBAC_MANIFEST = ModuleManifest(key="rbac", name="RBAC")

#: Every ClinicQ staff role opens the dashboard shell (Issue 18); what it shows inside is decided by
#: the module grants. At ``business``, like the kernel's ``user``: the shell has no rows to narrow.
#: The patient role has no dashboard.
DASHBOARD_MANIFEST = ModuleManifest(
    key="dashboard",
    name="Dashboard",
    grants=tuple(
        RoleGrant(
            role.value,
            "dashboard",
            PermissionVerb.READ.value,
            GrantScope.BUSINESS.value,
        )
        for role in (
            UserRole.RECEPTIONIST,
            UserRole.NURSE_DOCTOR,
            UserRole.CLINIC_MANAGER,
            UserRole.PLATFORM_ADMIN,
        )
    ),
)

# ``full_key`` overrides: ``logs`` and ``users`` are undotted keys under a dotted-composition
# parent, exactly the predating-the-framework case ``ResourceSpec.full_key`` exists for.
REPORTS_SHELL_MANIFEST = ModuleManifest(
    key="reports",
    name="Reports",
    children=(
        ResourceSpec(key="logs", full_key="logs", name="Logs"),
        ResourceSpec(key="users", full_key="users", name="Users"),
    ),
)
