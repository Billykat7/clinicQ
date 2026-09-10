"""Role permission model: cumulative CRUD verbs **and** named `(resource, action)` grants.

Ported from the ``maps`` project (Issue #5), extended for DENY (Issue #134) and named actions
(Issue #140, M25). A row is one of two shapes, discriminated by ``action``:

* **Cumulative CRUD grant** (``action IS NULL``) — the original model: ``max_verb`` plus an
  ``effect``. An ``ALLOW`` grants that verb and every lower verb (cumulative), a ``DENY`` removes it
  and every *higher* verb and wins over any ALLOW for the same resource+verb (deny-beats-allow).
  Resources form a parent→child tree so a parent grant cascades (see ``src.core.rbac``). This shape
  is unchanged, so every existing CRUD-gated route behaves exactly as before.
* **Named-action grant** (``action`` set, e.g. ``sign``) — the ``(resource, action)`` path from
  ``.btk/RBAC/rbac-design.md`` §1 (Issue #140). Its ``permission_id`` points at the catalog
  ``permissions`` row, ``max_verb`` is ``NULL`` (a named action is *not* a cumulative rung), and
  ``applies_to_descendants`` opts the grant into cascading the **same action** to descendant
  resources (resolved via ``resource_descendants``; default off — a grant stays scoped).

Two **partial unique indexes** keep at most one cumulative row per ``(role, resource)`` and one row
per ``(role, resource, action)`` — so a resource can carry a cumulative grant *and* independent
named-action grants at once.

Either shape also carries a **scope tier** (``scope``, Issue #156, M28), one rung of the ladder
``own < assigned < business`` (Issue #171): ``own`` (strictly the caller's own rows), ``assigned``
(those plus the rows reachable through the caller's ``user_roles(scope_type='property')``
assignments) or ``business`` (the whole business — the management/console view). It is the grant's
*condition*, the *whose rows* half of an authorization decision the verb alone cannot express —
what ``is_management_role()`` used to answer from the role's **name** (deleted in Issue #172), now a
per-grant property an admin sets. ``src.core.scope.resolve_scope`` resolves it and turns a
sub-business tier into the right narrowing for the resource's shape. A grant that says nothing about
its breadth is ``own``, the narrowest rung — deny-first for verbs, own-first for breadth.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema, GrantScope, PermissionEffect
from src.database.models.base import Base

_SCHEMA = DbSchema.CLINICQ.value


def _scope_in_clause() -> str:
    """Return the ``scope in (...)`` SQL predicate covering every :class:`GrantScope` member."""
    values = ", ".join(f"'{scope.value}'" for scope in sorted(GrantScope))
    return f"scope in ({values})"


class RolePermission(Base):
    """A grant for a role: a cumulative CRUD verb, or a named ``(resource, action)`` capability."""

    __tablename__ = "role_permission"
    __table_args__ = (
        # One cumulative (verb) grant per (role, resource) — the original invariant.
        Index(
            "uq_role_permission_cumulative",
            "role",
            "resource",
            unique=True,
            sqlite_where=text("action IS NULL"),
            postgresql_where=text("action IS NULL"),
        ),
        # One named-action grant per (role, resource, action), independent of the cumulative row.
        Index(
            "uq_role_permission_action",
            "role",
            "resource",
            "action",
            unique=True,
            sqlite_where=text("action IS NOT NULL"),
            postgresql_where=text("action IS NOT NULL"),
        ),
        CheckConstraint(
            "effect in ('allow', 'deny')",
            name="ck_role_permission_effect",
        ),
        # Rendered from :class:`~src.commons.enums.GrantScope` rather than spelled out, so adding a
        # rung to the ladder cannot leave the constraint behind (the mistake Issue #171 had to fix
        # by hand). Members are sorted for a stable DDL string across interpreter runs.
        CheckConstraint(
            _scope_in_clause(),
            name="ck_role_permission_scope",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL for a named-action grant (which is not a cumulative rung); set for a CRUD grant.
    max_verb: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # NULL marks a cumulative CRUD grant; a value (e.g. ``sign``) marks a named-action grant.
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Convenience FK to the catalog ``(resource, action)`` permission, set on named-action grants.
    permission_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{_SCHEMA}.permissions.id", ondelete="CASCADE"),
        nullable=True,
    )
    # ALLOW (default) adds access; DENY subtracts it and beats any ALLOW for the same target
    # (Issue #134). Defaulting to ALLOW keeps every pre-existing grant behaviour-identical.
    effect: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PermissionEffect.ALLOW.value,
        server_default=PermissionEffect.ALLOW.value,
    )
    # Opt-in cascade (Issue #140): when true, a named-action grant's action is pushed to every
    # descendant resource that defines it (via ``resource_descendants``); off by default, so a
    # grant stays scoped to its own resource. A narrower child DENY still overrides a cascaded ALLOW.
    applies_to_descendants: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    # How wide the grant reaches (Issue #156, M28) — one rung of the ``own < assigned < business``
    # ladder (Issue #171), narrowed per resource shape by ``src.core.scope.resolve_scope``.
    #
    # **Both defaults are the narrowest rung** (Issue #172, and migration ``0069`` moves the schema
    # one to match). They used to be ``business``, on the reasoning that a raw-SQL insert forgetting
    # the column should land the value the column had always effectively carried — which made the
    # widest tier the answer to "nothing was said about this grant's breadth". Deny-first for verbs
    # and allow-first for breadth is not a coherent position: an insert that says nothing now grants
    # the caller's own rows, and widening is an explicit, audited act. The ORM default is a flat
    # constant rather than the role-derived callable it replaced — the role's name decides nothing.
    scope: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GrantScope.OWN.value,
        server_default=GrantScope.OWN.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
