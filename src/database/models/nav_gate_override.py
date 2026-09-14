"""Nav-gate override catalog: which (resource, requirement) currently gates a surface node.

Added by Issue #149 (M26) as the DB half of the Option C hybrid in
``docs/architecture/rbac-universal-surface-framework.md`` §5: **structure** (icon/href/label/tab)
stays Python-declared — in ``nav_registry.py`` today, or a module's own routes+templates once
Issue #146 lands — because a genuinely new tab always needs a new route and template regardless of
where its metadata lives. Only the **gate** — which resource + verb/named-action an already-shipped
surface currently requires — lives here, so an admin can re-point an existing tab or button to a
different or finer permission from the console with no deploy.

``sync_module_manifest`` (``src.core.rbac_manifest_sync``) seeds one row per manifest node that
carries ``NavMeta``, insert-only: once a row exists for a ``surface_key`` it is DB-authoritative, so
a later sync (a redeployed manifest) never reverts a live admin re-gate.

Issue #165 (M28) added the second axis of that gate — ``scope``, the tier the caller's grant must
reach for the surface to open. Same rationale, same editability: which *permission* a surface checks
and how *wide* that permission must reach are both policy, and neither should need a deploy.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import GrantScope
from src.database.models.base import Base


class NavGateOverride(Base):
    """One surface node's current gate: a resource key plus a verb or a named action, not both."""

    __tablename__ = "nav_gate_overrides"
    __table_args__ = (
        CheckConstraint(
            "(verb IS NULL) != (action IS NULL)",
            name="nav_gate_overrides_verb_xor_action",
        ),
        # Every ``GrantScope`` tier (migration 0025 added ``assigned``, Issue 48).
        CheckConstraint(
            "scope in ('assigned', 'business', 'own')",
            name="ck_nav_gate_overrides_scope",
        ),
    )

    # The nav/tab/button identifier this row gates (conventionally a resource's own full key).
    surface_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(120), nullable=False)
    verb: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The :class:`~src.commons.enums.GrantScope` tier the caller's grant must reach for this surface
    # to open (Issue #165). ``business`` — the closed value — is the default in both Python and the
    # schema, so a row written without it is never an accidentally-widened surface.
    scope: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GrantScope.BUSINESS.value,
        server_default=GrantScope.BUSINESS.value,
    )
    # ``true`` once an admin has re-pointed this row from the console (Issue #146) — informational
    # today; the flag exists so a future console can tell "shipped default" from "live edit" apart.
    is_admin_override: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
