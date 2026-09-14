"""Visits RBAC manifest: the clinician's side of a visit (Issue 48; the notes arrive with Issue 53).

.. code-block:: text

    visits          a patient's visit, as the consulting room sees it (staff only)
    └── notes       the private visit notes a nurse or doctor writes (health information)

Only ``nurse_doctor`` holds a grant here, at the ``own`` tier: the queues they are assigned to
(Issue 28), which is what "their room" means. A receptionist and a clinic manager hold nothing, and
nothing inherits into this tree from ``queues`` or ``sites``, because a grant on the clinic's
operations must never open its patients' clinical notes (``docs/guideline.md``, non-negotiable 4).

The dashboard's room view is gated on ``visits.notes:update`` (``src/core/nav_registry.py``), so the
screen appears for exactly the people who may write in it.
"""

from src.commons.enums import GrantScope, PermissionVerb, ScopeShape, UserRole
from src.core.rbac_manifest import ModuleManifest, ResourceSpec, RoleGrant

MANIFEST = ModuleManifest(
    key="visits",
    name="Visits",
    description="A patient's visit as the consulting room sees it.",
    # A note hangs off a ticket in a queue: ``own`` narrows by the caller's assigned queues.
    scope_shape=ScopeShape.QUEUE,
    children=(
        ResourceSpec(
            key="notes",
            name="Visits / Notes",
            description="Private visit notes written in the consulting room.",
        ),
    ),
    grants=(
        RoleGrant(
            UserRole.NURSE_DOCTOR.value,
            "visits.notes",
            PermissionVerb.UPDATE.value,
            GrantScope.OWN.value,
        ),
    ),
)
