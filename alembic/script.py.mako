"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence
% if imports:
${imports}
% endif

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Apply the change."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Reverse the change."""
    ${downgrades if downgrades else "pass"}
