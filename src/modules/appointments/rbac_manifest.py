"""Appointments RBAC manifest: who may read a clinic's appointment book and who may shape it (Issue 80).

.. code-block:: text

    appointments        a clinic's appointment book: policy, weekly windows, day overrides,
                        generated slots, blocks and the per-day availability

The front desk and the nurses read the book, because they are the ones a patient asks "is there a
time on Thursday?". Shaping it (the policy, the windows, generation and blocking a range for a staff
absence) is the clinic manager's: ``update`` at ``assigned``. Booking a place for a patient arrives
with Issue 81 as a child of this resource.
"""

from src.commons.enums import GrantScope, PermissionVerb, ScopeShape, UserRole
from src.core.rbac_manifest import ModuleManifest, RoleGrant

_ASSIGNED = GrantScope.ASSIGNED.value

MANIFEST = ModuleManifest(
    key="appointments",
    name="Appointments",
    description="A clinic's appointment book: bookable slots and the ranges taken out of it.",
    # A slot, window or block hangs off a queue at a clinic (Issue 19).
    scope_shape=ScopeShape.QUEUE,
    grants=(
        RoleGrant(
            UserRole.RECEPTIONIST.value,
            "appointments",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.NURSE_DOCTOR.value,
            "appointments",
            PermissionVerb.READ.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.CLINIC_MANAGER.value,
            "appointments",
            PermissionVerb.UPDATE.value,
            _ASSIGNED,
        ),
        RoleGrant(
            UserRole.PLATFORM_ADMIN.value,
            "appointments",
            PermissionVerb.READ.value,
            GrantScope.BUSINESS.value,
        ),
    ),
)
