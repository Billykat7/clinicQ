"""Queues RBAC manifest: who may configure a queue, call next, and move tickets (Issue 18).

.. code-block:: text

    queues              a queue's configuration (a grant here cascades to every child)
    ├── call            calling the next patient (``update``)
    └── tickets         issuing a walk-in, marking arrived, no-show, cancel, done (``update``)
        └── priority    moving a ticket up for clinical priority, with a reason (Issue 46)

The difference between a receptionist and a nurse is the **tier** of the same grant, not a
different verb. A receptionist calls next on any queue at the sites they work at (``assigned``); a
nurse or doctor only on the queues they are assigned to (``own``). The site guard and the queue
assignment (Issues 19, 28) turn the tier into rows.
"""

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.core.rbac_manifest import ModuleManifest, ResourceSpec, RoleGrant

_ASSIGNED = GrantScope.ASSIGNED.value
_OWN = GrantScope.OWN.value

MANIFEST = ModuleManifest(
    key="queues",
    name="Queues",
    description="A clinic's queues and the tickets in them.",
    children=(
        ResourceSpec(
            key="call",
            name="Queues / Call next",
            description="Calling the next patient.",
        ),
        ResourceSpec(
            key="tickets",
            name="Queues / Tickets",
            description="The tickets in a queue.",
            children=(
                ResourceSpec(
                    key="priority",
                    name="Queues / Tickets / Priority",
                    description="Moving a ticket up for clinical priority.",
                ),
            ),
        ),
    ),
    grants=(
        RoleGrant(
            UserRole.RECEPTIONIST.value, "queues", PermissionVerb.READ.value, _ASSIGNED
        ),
        RoleGrant(
            UserRole.RECEPTIONIST.value,
            "queues.call",
            PermissionVerb.UPDATE.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.RECEPTIONIST.value,
            "queues.tickets",
            PermissionVerb.UPDATE.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.NURSE_DOCTOR.value, "queues", PermissionVerb.READ.value, _ASSIGNED
        ),
        RoleGrant(
            UserRole.NURSE_DOCTOR.value,
            "queues.call",
            PermissionVerb.UPDATE.value,
            _OWN,
        ),
        RoleGrant(
            UserRole.NURSE_DOCTOR.value,
            "queues.tickets",
            PermissionVerb.UPDATE.value,
            _OWN,
        ),
        RoleGrant(
            UserRole.CLINIC_MANAGER.value,
            "queues",
            PermissionVerb.DELETE.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.PLATFORM_ADMIN.value,
            "queues",
            PermissionVerb.READ.value,
            GrantScope.BUSINESS.value,
        ),
    ),
)
