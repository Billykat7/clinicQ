"""patient email: a second identity a one-time code can reach (Issue 219)

* ``patient.email``: the normalised address, unique when set, ``NULL`` for every patient who has
  never signed in with one — which is every patient that exists today, and every patient created by
  USSD, WhatsApp or the front desk from now on.
* ``patient.email_verified_at``: when the address was last proved with a code, the sibling of
  ``phone_verified_at``.

No backfill and nothing made mandatory: ``phone_e164`` stays exactly as it is, still unique and
still nullable (a dependant with no phone of their own, Issue 84). A patient may now hold either
contact, or both, and the two unique indexes are what keep one contact to one patient.

The constraint is named the way ``uq_patient_phone_e164`` and ``uq_patient_whatsapp_id`` are
(``uq_%(table_name)s_%(column_0_name)s``, the metadata's convention), so autogenerate stays quiet.

Revision ID: 0047
Revises: 0046
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the address and when it was last proved."""
    op.add_column(
        "patient",
        sa.Column("email", sa.String(length=254), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "patient",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # One address, one patient. Unique over a nullable column: PostgreSQL treats NULLs as distinct,
    # so every patient without an address is unaffected by it.
    op.create_unique_constraint(
        op.f("uq_patient_email"), "patient", ["email"], schema=SCHEMA
    )


def downgrade() -> None:
    """Drop them. No patient's number, ticket or session depends on either."""
    op.drop_constraint(
        op.f("uq_patient_email"), "patient", type_="unique", schema=SCHEMA
    )
    op.drop_column("patient", "email_verified_at", schema=SCHEMA)
    op.drop_column("patient", "email", schema=SCHEMA)
