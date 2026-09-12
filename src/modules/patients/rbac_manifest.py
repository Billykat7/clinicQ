"""Patients RBAC manifest: the resources patient records are gated by (Issue 17).

* ``patients``: staff access to patient records (looking a patient up at reception, the DSAR
  export of Issue 96). No route uses it yet.
* ``patients.self``: a patient acting on their own record: their profile and, from Issue 21, their
  consent. The ``patient`` role holds ``update`` on it at the ``own`` tier (Issue 18), enforced on
  every patient route by :func:`src.api.rbac_deps.require_patient`.
"""

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.core.rbac_manifest import ModuleManifest, ResourceSpec, RoleGrant

MANIFEST = ModuleManifest(
    key="patients",
    name="Patients",
    description="Patient records: a verified phone number, and what the patient chose to share.",
    children=(
        ResourceSpec(
            key="self",
            name="Patients / Own record",
            description="A patient's own record, reached through their own session.",
        ),
    ),
    grants=(
        RoleGrant(
            UserRole.PATIENT.value,
            "patients.self",
            PermissionVerb.UPDATE.value,
            GrantScope.OWN.value,
        ),
    ),
)
