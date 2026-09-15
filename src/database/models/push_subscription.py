"""Push subscription: where to deliver a web push to one patient's browser (Issue 64).

A browser that agreed to notifications gives the page a subscription: an **endpoint** on its push
service (Google's for Chrome on Android, Mozilla's for Firefox, Apple's for Safari) and two keys the
payload is encrypted to (``p256dh``, ``auth``), so the push service carries a message it cannot read.

* **One row per endpoint.** ``endpoint_hash`` (SHA-256 of the endpoint) is unique: subscribing again from
  the same browser updates the row, and a browser handed to another patient moves to that patient.
* **A patient may have several**, one per phone or browser; the newest is tried first.
* **Dead rows go at the first failed send.** A push service answering ``404`` or ``410`` means the browser
  unsubscribed or the subscription expired, and the row is deleted then (Issue 64). ``expires_at`` is the
  browser's own expiry when it gave one; an expired row is never used.
* **Deleted with the patient** (``CASCADE``): a subscription reaches a person, and means nothing without one.

Datetimes are Africa/Johannesburg.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value


class PushSubscription(Base, TimestampMixin):
    """One browser's push subscription, for one patient."""

    __tablename__ = "push_subscription"
    __table_args__ = (
        Index("uq_push_subscription_endpoint_hash", "endpoint_hash", unique=True),
        Index("ix_clinicq_push_subscription_patient", "patient_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The patient the browser belongs to."""
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    """The push service URL messages are POSTed to. Only an allowed push service host is accepted."""
    endpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    """SHA-256 of ``endpoint``, hex: the unique key (an endpoint can be longer than an index allows)."""
    p256dh: Mapped[str] = mapped_column(String(128), nullable=False)
    """The browser's P-256 public key, base64url: the payload is encrypted to it."""
    auth: Mapped[str] = mapped_column(String(48), nullable=False)
    """The browser's 16-byte authentication secret, base64url."""
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the browser said the subscription expires; ``NULL`` when it did not say."""
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When a push service last accepted a message for it."""
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """The browser that subscribed, for a patient telling their phones apart. Truncated."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: never the endpoint, which is a credential."""
        return f"PushSubscription(id={self.id!r}, patient_id={self.patient_id!r})"
