"""Role inheritance edges: a role inherits another role's grants (Issue #135).

Roles compose as a **DAG**, not a single-parent tree — a real org role is often "everything a
manager can do, plus everything an auditor can do", inheriting *multiple* parents. Each edge here
says ``role`` inherits ``inherits_role``; a role's effective grants are the union over its **role
closure** (itself + everything reachable through these edges), with deny-beats-allow applied after
the union (see ``src.core.rbac``). No edges are seeded, so the current flat roles keep resolving
exactly as before.

Both endpoints reference ``rbac_role.name`` with ``ON DELETE CASCADE`` (deleting a role removes the
edges that mention it), the primary key is the pair, and a ``CHECK`` forbids a self-edge. Cycles
are rejected at the API layer and the closure walk is cycle-guarded regardless, so malformed data
can never loop.
"""

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class RoleHierarchy(Base):
    """One inheritance edge: ``role`` inherits the grants of ``inherits_role``."""

    __tablename__ = "role_hierarchy"
    __table_args__ = (
        CheckConstraint(
            "role <> inherits_role",
            name="ck_role_hierarchy_no_self_edge",
        ),
    )

    role: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("rbac_role.name", ondelete="CASCADE"),
        primary_key=True,
    )
    inherits_role: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("rbac_role.name", ondelete="CASCADE"),
        primary_key=True,
    )
