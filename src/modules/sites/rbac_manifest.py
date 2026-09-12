"""Sites RBAC manifest: what each role may do to a clinic (Issue 18).

The tree is the grant surface: ``sites`` is the clinic, and each child is independently grantable,
so a receptionist can read the display settings without being able to change them, and a nurse can
see their colleagues without seeing the reports.

.. code-block:: text

    sites               the clinic (a grant here cascades to every child)
    ├── profile         name, hours, sector, location
    ├── settings        operational settings (Issue 27)
    ├── display         the waiting-room board's display mode (Issue 27)
    ├── staff           who works there: invitations, deactivation (Issues 22, 28)
    └── reports         wait times, no-shows, channel mix (M12)

Grants at the ``assigned`` tier reach the sites a member holds a role at; the site guard (Issue 19)
turns that into rows. ``platform_admin`` reaches every clinic at ``business``.
"""

from src.commons.enums import GrantScope, PermissionVerb, ScopeShape, UserRole
from src.core.rbac_manifest import ModuleManifest, ResourceSpec, RoleGrant

_ASSIGNED = GrantScope.ASSIGNED.value

MANIFEST = ModuleManifest(
    key="sites",
    name="Sites",
    description="A clinic and everything configured for it.",
    # Every row under this tree is a clinic, or carries its ``site_id`` (Issue 19).
    scope_shape=ScopeShape.SITE,
    children=(
        ResourceSpec(
            key="profile", name="Sites / Profile", description="Name, hours, sector."
        ),
        ResourceSpec(
            key="settings", name="Sites / Settings", description="Operational settings."
        ),
        ResourceSpec(
            key="display",
            name="Sites / Display",
            description="The board's display mode.",
        ),
        ResourceSpec(
            key="staff", name="Sites / Staff", description="Who works at the clinic."
        ),
        ResourceSpec(
            key="reports", name="Sites / Reports", description="The clinic's reports."
        ),
    ),
    grants=(
        # Front desk: sees the clinic, its board mode and colleagues; changes none of them.
        RoleGrant(
            UserRole.RECEPTIONIST.value,
            "sites.profile",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.RECEPTIONIST.value,
            "sites.display",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.RECEPTIONIST.value,
            "sites.staff",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        # Nurse or doctor: the clinic and colleagues.
        RoleGrant(
            UserRole.NURSE_DOCTOR.value,
            "sites.profile",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.NURSE_DOCTOR.value,
            "sites.staff",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        # Clinic manager: runs the clinic; may invite and deactivate its staff.
        RoleGrant(
            UserRole.CLINIC_MANAGER.value,
            "sites",
            PermissionVerb.UPDATE.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.CLINIC_MANAGER.value,
            "sites.staff",
            PermissionVerb.DELETE.value,
            _ASSIGNED,
        ),
        # The operator: onboards and supports every clinic.
        RoleGrant(
            UserRole.PLATFORM_ADMIN.value,
            "sites",
            PermissionVerb.DELETE.value,
            GrantScope.BUSINESS.value,
        ),
    ),
)
