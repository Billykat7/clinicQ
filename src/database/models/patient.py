"""Patient model: something a one-time code can reach, and nothing that needs a password (Issue 17).

A patient is never an account. Asking someone to create a password before they can join a queue
would shut out the USSD and WhatsApp channels and most of the walk-in population with them, so the
whole identity is a contact the patient proves with a one-time code (or that a USSD or WhatsApp
gateway vouches for). There is **no password column**, and no column that could hold one;
``tests/integration/patients/test_patient_otp.py`` checks the table for it.

* ``phone_e164`` is the identity: unique, and only ever written through
  :func:`src.commons.phone.normalize_phone`, so ``0821234567``, ``27821234567`` and
  ``+27821234567`` are one patient, never three.
* ``email`` is the **second** identity (Issue 219), and the same shape: unique, and only ever
  written through :func:`src.commons.email_address.normalize_email`, so ``Nomsa@Gmail.COM`` and
  ``nomsa@gmail.com`` are one patient. A patient who signed in with an address is a patient like
  any other — they join queues, hold tickets and open ``/t/`` — and they may have no number at
  all, which is why neither column is mandatory and both are unique. Offered only where
  ``PATIENT_EMAIL_SIGN_IN_ENABLED`` is on.
* ``whatsapp_id`` is the id WhatsApp reports for the conversation, when the patient has used it.
* ``display_name`` is optional and shown nowhere without consent (Issue 21 gates the board).
* ``session_version`` ends every web session at once: a patient token carries the version it was
  issued under, and signing out increments it, so every earlier token is refused. A counter, not a
  timestamp, so a token issued in the same second as the sign-out cannot slip through.
  Datetimes are aware; business time is Africa/Johannesburg.
* Soft-deleted, never hard-deleted by the application: the POPIA erasure workflow (Issue 96) decides
  what an erasure removes.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import SoftDeleteMixin, TimestampMixin


class Patient(Base, TimestampMixin, SoftDeleteMixin):
    """One patient: a verified phone number, optionally a WhatsApp id and a display name."""

    __tablename__ = "patient"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    phone_e164: Mapped[str | None] = mapped_column(
        String(16), nullable=True, unique=True
    )
    """The normalised number (``+27821234567``): the patient's identity. E.164 is at most 15 digits.

    ``None`` only for a **dependant with no phone of their own** (Issue 84): a small child whose parent
    holds the phone. Such a record is created by the person who acts for them, can never sign in, and
    its messages go to that person's phone. Everyone else has a number, and it is unique."""
    whatsapp_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, unique=True
    )
    """WhatsApp's id for the patient, set when they reach ClinicQ on WhatsApp (Issue 75)."""
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, unique=True)
    """The normalised address (``nomsa@gmail.com``): the patient's second identity (Issue 219).

    ``None`` for every patient who has never signed in with one, which is most of them and every
    patient created by USSD, WhatsApp or the front desk. Unique when set. RFC 5321's 254 characters.

    Unrelated to a staff account's address: the same person may hold both, and one never
    authenticates the other."""
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """What the patient asked to be called. Never on a public screen without consent (Issue 21)."""
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the patient last proved the number with a one-time code (Africa/Johannesburg)."""
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the patient last proved the address with a one-time code (Africa/Johannesburg)."""
    last_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """The channel of the patient's latest visit (:class:`~src.commons.enums.PatientChannel`)."""
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the patient was last active on any channel (Africa/Johannesburg)."""
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """Incremented by a sign-out; a session token issued under an older version is refused."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: the id, never the number."""
        return f"Patient(id={self.id!r})"
