"""Patient notification preference: how a patient with no account wants to be reached (Issue 63).

The account-holder preferences (:class:`~src.database.models.notification_preference.NotificationPreference`)
are keyed by user, and a patient is never a user (Issue 17). This row is the patient's own, one per
patient, and it starts with the one thing the notification service needs to pick a transport: the
patient's **preferred transport**. When it can reach them it is tried first; otherwise, or when it
fails, the free-first fallback chain (:data:`~src.commons.enums.PATIENT_TRANSPORT_CHAIN`) takes over.

The row is optional: a patient who never chose gets the chain as it stands.

Issue 67 adds the rest of the patient's own preferences to this row: quiet hours, the events they do not want
to hear about, and a global opt-out (``opted_out_at``). They are editable without an account, from the ticket
page's link, and by replying STOP to an SMS.

Consent is **not** here. Whether a patient may be messaged at all is ``patient_consent`` (Issue 21);
this row only says how, once that answer is yes.
"""

from datetime import datetime, time
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value


class PatientNotificationPreference(Base, TimestampMixin):
    """One patient's notification preferences."""

    __tablename__ = "patient_notification_preference"

    patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        primary_key=True,
    )
    """The patient. ``CASCADE``: a preference means nothing without its patient."""
    preferred_channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    """:class:`~src.commons.enums.NotificationChannel` to try first; ``NULL`` means the chain's order."""
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    """The language the patient reads messages in (Issue 66), one of
    :data:`~src.commons.enums.NOTIFICATION_LANGUAGES`; ``NULL`` uses the clinic's board language."""
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    """When the patient's quiet hours begin, Johannesburg wall-clock time (Issue 67); ``NULL`` for none."""
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    """When they end; a window may wrap past midnight (22:00 to 06:00)."""
    muted_events: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    """:class:`~src.commons.enums.PatientEvent` values the patient does not want to be told about."""
    opted_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the patient stopped every message, on every channel (Issue 67); ``NULL`` while they have not.
    Keyed by the patient, not a clinic, so it holds wherever they join next."""
    opted_out_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """Where the patient last changed these settings: :class:`~src.commons.enums.PreferenceSource`."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"PatientNotificationPreference(patient_id={self.patient_id!r})"
