"""SMS spending controls: the run-time kill switch, the once-a-day cap alerts, and receipt events (Issue 65).

SMS is the one transport that costs money per message, so its guard rails are data, not code:

* :class:`PlatformSwitchState` is a switch an operator flips from the API. It is read on every send, so
  the SMS kill switch stops the next message with no deploy and no restart.
* :class:`SmsCapAlert` remembers that a cap was hit today, by what (a clinic or a patient), so the team is
  alerted **once** per clinic or patient per day rather than once per blocked message. The blocked
  messages themselves are still each recorded on the ledger.
* :class:`SmsDeliveryEvent` is one delivery receipt the gateway called back with, keyed by the gateway's
  message id and the status, so a receipt the gateway delivers twice is applied once.

None of these carries a ``site_id`` column: the kill switch is platform-wide, and a clinic's own cap lives on
``site.sms_daily_cap``. Datetimes are Africa/Johannesburg.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class PlatformSwitchState(Base, TimestampMixin):
    """One run-time switch (:class:`~src.commons.enums.PlatformSwitch`) and who last flipped it."""

    __tablename__ = "platform_switch"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    """:class:`~src.commons.enums.PlatformSwitch`."""
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether the switch is on. For ``sms_kill``, on means no SMS is sent."""
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """Why it was last flipped, in the operator's words."""
    changed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Who last flipped it (their email)."""
    changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When it was last flipped (Africa/Johannesburg)."""


class SmsCapAlert(Base, TimestampMixin):
    """A cap that was reached today, for one clinic or one patient: the team was told once."""

    __tablename__ = "sms_cap_alert"
    __table_args__ = (
        UniqueConstraint(
            "reason", "subject_id", "service_day", name="uq_sms_cap_alert_day"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    """:class:`~src.commons.enums.SmsBlockReason`: the site's or the patient's cap."""
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    """The clinic's id for a site cap, the patient's id for a patient cap."""
    service_day: Mapped[date] = mapped_column(Date, nullable=False)
    """The Johannesburg day the cap was reached."""


class SmsDeliveryEvent(Base):
    """One delivery receipt from the SMS gateway, recorded before it is applied (idempotency)."""

    __tablename__ = "sms_delivery_event"

    id: Mapped[str] = mapped_column(String(320), primary_key=True)
    """``<provider>:<gateway message id>:<gateway status>``: a replay of the same receipt collides here."""
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    """The gateway's own word (``Success``, ``Failed``, ``Buffered``…)."""
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    """:class:`~src.commons.enums.SmsDeliveryState`: what it means for the ledger."""
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SmsInboundEvent(Base):
    """One SMS a patient replied with, recorded before it is applied (Issue 67): a replay changes nothing twice.

    Keeps the keyword and the patient it applied to, never the number or the rest of the text.
    """

    __tablename__ = "sms_inbound_event"

    id: Mapped[str] = mapped_column(String(320), primary_key=True)
    """``<provider>:<gateway message id>``."""
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    keyword: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """The first word, when it was a keyword (``STOP``, ``START``); ``NULL`` otherwise."""
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    """``stopped``, ``restarted`` or ``ignored``."""
    patient_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The patient the reply applied to; ``NULL`` for a number no patient has."""
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
