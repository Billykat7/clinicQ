"""One phone acting for another person: a proxy and their dependant (Issue 84).

One household often shares one smartphone. A daughter books for her mother; a parent joins a queue for
a child. The row here says who may act for whom, and nothing else moves: **the ticket and the visit
belong to the person being seen**, and the board shows them under their own consent (Issue 58). Only
the messages go to the proxy's phone, because that is the phone that exists.

What makes it defensible:

* **a link to a number is made only with a code sent to that number** (``verified_at``), so nobody can
  attach themselves to a stranger's record. A dependant with no phone of their own — a small child — is
  a new record with no identity to take over, and is created without a code;
* **the person being seen consents** (``ConsentPurpose.PROXY_ACTIONS``, recorded against them by the
  proxy at the moment of the link), and the wording says either of them can end it;
* **ending it is immediate**: ``revoked_at`` is set, and the next action the proxy tries is refused.
  The row stays, so the audit trail still explains the tickets that were issued while it was live;
* **every action names both people**: the audit row's actor is the proxy, its entity is the dependant,
  and ``ticket.proxy_patient_id`` keeps the pair on the ticket itself.

Datetimes are aware; business time is Africa/Johannesburg.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema, ProxyRelationship
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value


class PatientLink(Base, TimestampMixin):
    """One person may act for another: who, for whom, how they are related, and until when."""

    __tablename__ = "patient_link"
    __table_args__ = (
        UniqueConstraint(
            "proxy_patient_id", "dependant_patient_id", name="uq_patient_link_pair"
        ),
        Index("ix_clinicq_patient_link_proxy", "proxy_patient_id", "revoked_at"),
        Index("ix_clinicq_patient_link_dependant", "dependant_patient_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    proxy_patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The person acting: the phone that books, joins and receives the messages."""
    dependant_patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The person being seen. Every ticket and booking made through this link is theirs."""
    relationship_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    """How the proxy described the relationship (:class:`~src.commons.enums.ProxyRelationship`)."""
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the dependant's own number was proved with a code; ``None`` for a dependant with no phone."""
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the link was ended. From that moment the proxy is refused; the row stays for the record."""
    revoked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The patient who ended it: either of the two. No foreign key: the record outlives an account."""

    @property
    def relationship_enum(self) -> ProxyRelationship:
        """``relationship_kind`` as its enum member, for code that compares rather than renders."""
        return ProxyRelationship(self.relationship_kind)

    @property
    def is_active(self) -> bool:
        """Whether the proxy may act right now."""
        return self.revoked_at is None

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: ids only, never a name or a number."""
        return (
            f"PatientLink(proxy_patient_id={self.proxy_patient_id!r}, "
            f"dependant_patient_id={self.dependant_patient_id!r})"
        )
