"""Named RBAC roles with metadata (Issue #5).

Ported from the ``maps`` project. ``name`` matches ``user.role`` and
``role_permission.role``. System roles (``user``, ``admin``) ship seeded and carry
``is_system=true`` so operator tooling can protect them from deletion. Permissions
themselves live in ``role_permission`` keyed by this role's ``name``.
"""

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class RbacRole(Base, TimestampMixin):
    """Application role definition (metadata + system flag)."""

    __tablename__ = "rbac_role"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    # A role holding this sees every property regardless of any ScopeResolver assignment
    # (Issue #147, M26) — the "admin/IT/sysadmin see everything" boundary, inverted correctly:
    # exempt roles opt OUT of scoping, not into it. Seeded ``true`` for ``admin`` only (this
    # codebase has no separate IT/sysadmin role); an admin can mark a future role exempt from
    # the RBAC console. ``False`` by default, including for ``manager`` — a manager-tier role is
    # scoped by its ``UserRoleAssignment(scope_type='property')`` rows, or unrestricted only in
    # the absence of any such row (see ``src.core.scope.property_ids_in_scope``).
    is_scope_exempt: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
