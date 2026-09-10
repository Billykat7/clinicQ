"""Documents RBAC manifest.

The module ships two resources rather than one because they answer different questions:

* ``documents`` — the store itself. In practice a document's access is inherited from the record
  it hangs off (``OWNER_TYPE_RESOURCE`` in this module's ``enums.py`` maps each owner type to the
  resource that governs it), so this root exists for the operations that are about the *store* —
  the retention sweep, an admin listing — rather than about one record.
* ``document.signature`` — the e-signature flow, and a child on purpose: sending a document for
  signature is a different capability from reading it, and ``sign`` is a **named action** rather
  than a rung on the CRUD ladder, because signing is not "more than" updating.

Grants nothing by default. Decide who signs before anyone can.
"""

from src.commons.enums import PermissionAction
from src.core.rbac_manifest import ModuleManifest, ResourceSpec

MANIFEST = ModuleManifest(
    key="documents",
    name="Documents",
    description="Stored files and their retention.",
    children=(
        ResourceSpec(
            key="signature",
            full_key="document.signature",
            name="Documents / Signature",
            description="Sending a document for signature, and the envelopes it produces.",
            named_actions=(PermissionAction.SIGN.value,),
        ),
    ),
)
