"""Patient notification preference: how a patient with no account wants to be reached (Issue 63).

The account-holder preferences (:class:`~src.database.models.notification_preference.NotificationPreference`)
are keyed by user, and a patient is never a user (Issue 17). This row is the patient's own, one per
patient, and it starts with the one thing the notification service needs to pick a transport: the
patient's **preferred transport**. When it can reach them it is tried first; otherwise, or when it
fails, the free-first fallback chain (:data:`~src.commons.enums.PATIENT_TRANSPORT_CHAIN`) takes over.

The row is optional: a patient who never chose gets the chain as it stands. Issue 67 adds language,
quiet hours and per-event opt-outs to this same row, reachable by ticket reference.

Consent is **not** here. Whether a patient may be messaged at all is ``patient_consent`` (Issue 21);
this row only says how, once that answer is yes.
"""

from sqlalchemy import ForeignKey, String
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

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"PatientNotificationPreference(patient_id={self.patient_id!r})"
