"""Patient consent: the current answer, and the history that proves it (Issue 21).

Two tables, because they answer two questions:

* :class:`PatientConsent` is the **effective state**: one row per patient per purpose, the answer
  the board and the notification service read. A purpose with no row is **not granted** — the most
  private answer is the absence of a decision, so a patient who was never asked, or whose row was
  never written, is never treated as having agreed.
* :class:`PatientConsentEvent` is the **history**: one row per answer ever given, with the channel
  it came through and the wording the patient was shown. Never updated, never deleted — it is what
  proves consent was given (POPIA s11(2)), and what shows when it was withdrawn.

Withdrawal is an event like any other, with ``granted`` false, and it takes effect the moment the
current row is updated: the board's next render and the next send both read the current row.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class PatientConsent(Base, TimestampMixin):
    """The current answer for one patient and one purpose. Absent means not granted."""

    __tablename__ = "patient_consent"
    __table_args__ = (
        UniqueConstraint("patient_id", "purpose", name="uq_patient_consent_patient_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    """One of :class:`~src.commons.enums.ConsentPurpose`."""
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """The current answer. A row is written only when a patient answers, either way."""
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    """When this answer was given (Africa/Johannesburg)."""
    source_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    """The channel the latest answer came through (:class:`~src.commons.enums.PatientChannel`)."""
    wording_version: Mapped[str] = mapped_column(String(32), nullable=False)
    """Which version of the plain-language wording the patient was shown."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: never the patient's details."""
        return (
            f"PatientConsent(patient_id={self.patient_id!r}, purpose={self.purpose!r})"
        )


class PatientConsentEvent(Base):
    """One answer, kept for good: what was asked, what was said, where, and when."""

    __tablename__ = "patient_consent_event"
    __table_args__ = (
        Index(
            "ix_clinicq_patient_consent_event_patient",
            "patient_id",
            "purpose",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """True when consent was given, false when it was withdrawn or refused."""
    source_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    wording_version: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The staff member who recorded it, when a patient answered at the desk rather than on their
    own device. NULL when the patient answered for themselves."""
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The clinic the answer was given at, when it was given at one."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    """When it was recorded. There is deliberately no ``modified_at``: history does not change."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"PatientConsentEvent(patient_id={self.patient_id!r}, "
            f"purpose={self.purpose!r}, granted={self.granted!r})"
        )
