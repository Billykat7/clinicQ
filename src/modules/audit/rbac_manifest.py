"""Audit RBAC manifest: who may read the trail across every clinic (Issue 20).

Two audiences, two resources, deliberately:

* ``audit`` (here) is the **platform-wide** trail — every clinic's rows at once. The operator's
  console reads it (``platform_admin``), and the kernel's administrator, who configures the
  platform, holds it too. A grant here is a grant over every clinic, which is why it is its own
  resource and not a child of ``sites``.
* ``sites.audit`` (the sites manifest) is **one clinic's** trail, reached through the site guard,
  which is what a clinic manager holds by way of their grant on ``sites``.

Reading either is itself audited (``src/modules/audit/router.py``).
"""

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.core.rbac_manifest import ModuleManifest, RoleGrant

MANIFEST = ModuleManifest(
    key="audit",
    name="Audit trail",
    description="The append-only record of who did what, across every clinic.",
    grants=(
        RoleGrant(
            UserRole.PLATFORM_ADMIN.value,
            "audit",
            PermissionVerb.READ.value,
            GrantScope.BUSINESS.value,
        ),
        RoleGrant(
            UserRole.ADMIN.value,
            "audit",
            PermissionVerb.READ.value,
            GrantScope.BUSINESS.value,
        ),
    ),
)
