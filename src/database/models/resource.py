"""Resource catalog: the hierarchical resource tree, in the database (Issue #139, M25).

Until M25 the resource catalog was hard-coded — ``PermissionResource`` (a ``StrEnum``) and the
static ``PERMISSION_RESOURCE_PARENT`` map in ``src.commons.enums`` — so adding a resource meant a
code change, a migration and a release. This model moves the catalog into a table so it becomes
editable data (the admin CRUD arrives in Issue #141), following ``.btk/RBAC/rbac-design.md`` §1:
resources are hierarchical via a self-referential ``parent_id`` foreign key (not a boolean flag),
which supports arbitrary depth and lets ancestors/descendants be walked with a recursive query.

``key`` is the stable dotted identifier (``lease``, ``lease.signature``). It mirrored a
``PermissionResource`` value one-for-one while that enum survived as a compat shim; the enum was
permanently deleted in Issue #154 (M27), and the tree's shape now comes from the module manifests
registered in ``src.core.rbac_manifest_registry``, with a guard test asserting the seeded rows
reproduce them exactly. The flattened closure of this tree is precomputed into ``resource_descendants``
(see :class:`~src.database.models.resource_descendant.ResourceDescendant`).
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.database.models.base import Base

_SCHEMA = DbSchema.CLINICQ.value


class Resource(Base):
    """One catalog resource: a node in the self-referential resource hierarchy."""

    __tablename__ = "resources"
    __table_args__ = (Index("ix_resources_parent_id", "parent_id"),)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    # Self-FK to the parent resource; NULL for a root. ``ON DELETE CASCADE`` mirrors the design
    # spec (deleting a subtree removes its descendants) — the admin API (Issue #141) additionally
    # guards against deleting a resource still referenced by a grant before it ever gets here.
    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{_SCHEMA}.resources.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Stable dotted key (``lease.signature``); the value a module manifest declares.
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ``true`` protects a built-in catalog row from deletion in the admin UI (Issue #141); every
    # seeded resource is a system row (``.btk/RBAC`` §2, the ``is_system`` guard on ``roles``).
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
