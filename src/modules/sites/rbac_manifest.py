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
    ├── onboarding      whether it is listed, and how far its setup has got (Issues 29, 221)
    ├── reports         wait times, no-shows, channel mix (M12)
    └── audit           the clinic's own audit trail (Issue 20)

Grants at the ``assigned`` tier reach the sites a member holds a role at; the site guard (Issue 19)
turns that into rows. ``platform_admin`` reaches every clinic at ``business``.

``onboarding`` is its own resource rather than a verb on ``sites`` because **deciding whether a
clinic is listed is not the same authority as removing one** (Issue 221). It used to be: the
verification routes gated on ``sites`` + ``delete``, the widest grant the resource has, so nobody
could be given authority to check clinics without also being given authority to delete them, and
``/admin/rbac`` answered "who may approve a clinic?" with the delete grant. Splitting it also gives
a clinic manager somewhere to *read* their own clinic's setup checklist and listing state without
being able to decide either.
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
            key="onboarding",
            name="Sites / Onboarding",
            description="Whether a clinic is listed, and how far its own setup has got.",
        ),
        ResourceSpec(
            key="reports", name="Sites / Reports", description="The clinic's reports."
        ),
        ResourceSpec(
            key="audit",
            name="Sites / Audit trail",
            description="What happened at this clinic, and who did it (Issue 20).",
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
        # Clinic manager: runs the clinic; may invite and deactivate its staff. The grant below
        # cascades to ``sites.onboarding`` at ``assigned``, which is how a manager reads their own
        # clinic's setup checklist and re-sends its setup link (Issue 223). It does **not** let them
        # decide their own clinic's listing: the verification routes ask for ``business``, and a
        # manager holds nothing at that tier.
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
        # The operator: onboards and supports every clinic. Cascades to ``sites.onboarding`` at
        # ``business``, which is what the verification queue and its decision ask for — so no
        # platform admin gains or loses anything from the split. What the split buys is a grant an
        # operator can hand out on its own: ``sites.onboarding`` without ``sites`` approves clinics
        # and deletes none, which is the verification officer a pilot wants and could not be
        # expressed while approving *was* ``sites:delete``.
        RoleGrant(
            UserRole.PLATFORM_ADMIN.value,
            "sites",
            PermissionVerb.DELETE.value,
            GrantScope.BUSINESS.value,
        ),
    ),
)
