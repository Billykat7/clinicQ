"""nav_gate_overrides: a surface may require the ``assigned`` tier (Issue 48)

One check constraint widened; no data is rewritten on the way up.

Issue #171 put the ``assigned`` tier between ``own`` and ``business`` (``GrantScope``), and
``role_permission`` has allowed it since the baseline. ``nav_gate_overrides.scope`` was not updated
with it, so a surface could not *require* the tier a receptionist's grant is written at. The clinic
dashboard needs exactly that: the front desk opens for a grant on ``queues.tickets`` that reaches the
whole clinic (``assigned``), and not for a nurse's grant on the same resource, which reaches only
their own queues (``own``). ``make seed-rbac`` writes that default row, and the old constraint
refused it.

Downgrade narrows the constraint again. A row requiring ``assigned`` is first moved to ``business``,
the closed tier: the surface then opens for fewer people, never for more.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: The constraint's name under the metadata naming convention (as the baseline created it).
_CONSTRAINT = "ck_nav_gate_overrides_ck_nav_gate_overrides_scope"


def upgrade() -> None:
    """Allow ``assigned`` alongside ``own`` and ``business``."""
    op.drop_constraint(
        op.f(_CONSTRAINT), "nav_gate_overrides", type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "nav_gate_overrides",
        "scope in ('assigned', 'business', 'own')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Move any ``assigned`` surface to ``business``, then allow only ``own`` and ``business``."""
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.nav_gate_overrides SET scope = 'business' "
            "WHERE scope = 'assigned'"
        )
    )
    op.drop_constraint(
        op.f(_CONSTRAINT), "nav_gate_overrides", type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "nav_gate_overrides",
        "scope in ('business', 'own')",
        schema=SCHEMA,
    )
