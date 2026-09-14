"""Queue model: one named line at a clinic, which is what a patient actually joins (Issue 25).

A real clinic visit is triage, then a consulting room, then the pharmacy window. A system that
models one line per clinic is abandoned in the second week of a pilot, so a site carries **1..n**
queues from the start, and every ticket in M6 belongs to one of them.

Four columns are worth explaining, because other milestones are built on them:

* **``display_order``** is what the board and the channel menus sort by, so a clinic decides that
  triage comes before the pharmacy rather than the alphabet deciding it.
* **``is_active``** hides a queue from *new joins* while leaving its history queryable. Deleting a
  queue would orphan yesterday's tickets, and "how long did the pharmacy take last month" is a
  question a clinic has to be able to ask about a line it has since closed.
* **``allows_remote_join``** is enforced on the **server** (:func:`src.modules.queues.service
  .ensure_remote_join_allowed`), not by hiding a button. A walk-in-only queue that a USSD session
  can still join is a walk-in-only queue in the user interface alone.
* **``expected_service_minutes``** is the wait estimator's prior until it has real samples
  (Issue 42), which is why it is on the queue rather than only on the services catalogue.

``name`` and ``slug`` are unique **per site**, not platform-wide: every clinic has a queue called
Triage, and they are different queues.
"""

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import QueueKind
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin

#: The widest a queue's expected service time may sensibly be. Above this somebody has typed hours
#: into a minutes field, and the estimator would report a wait measured in days.
MAX_EXPECTED_SERVICE_MINUTES = 240
#: The range a recall timeout may be set in, in minutes (Issue 43). Under a minute nobody reaches a
#: room; over an hour the room has stood empty for the whole of it.
RECALL_TIMEOUT_RANGE = (1, 60)
#: The most tickets one queue may be configured to issue in a service day. A ceiling rather than a
#: target: it exists so a mistyped capacity cannot silently cap a clinic at three patients.
MAX_DAILY_CAPACITY = 2000


class Queue(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """One named line at one clinic: Triage, Room 2, the pharmacy window."""

    __tablename__ = "queue"
    __table_args__ = (
        # Unique per site, because every clinic has a Triage and they are different queues.
        UniqueConstraint("site_id", "slug", name="uq_queue_site_id_slug"),
        UniqueConstraint("site_id", "name", name="uq_queue_site_id_name"),
        # What the board and the channel menus read: this clinic's live queues, in display order.
        Index(
            "ix_clinicq_queue_site_active_order",
            "site_id",
            "is_active",
            "display_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clinicq.site.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    """What patients and staff call it: "Triage", "Doctor Room 2", "Pharmacy"."""
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    """The stable handle a channel menu and a seeded fixture use. Unique within the site."""
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=QueueKind.OTHER.value
    )
    """:class:`~src.commons.enums.QueueKind`, so a board and a report recognise a pharmacy queue."""
    room_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    """Where to send the patient: "Room 2", "Counter B". Shown on the board when it is called."""
    ticket_prefix: Mapped[str] = mapped_column(String(4), nullable=False, default="A")
    """The letter a ticket number starts with, so two queues' numbers cannot be confused."""
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Lower comes first. A clinic's order, not the alphabet's."""
    expected_service_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
    """The estimator's prior until real samples exist (Issue 42)."""
    max_daily_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """``None`` means no cap. A number is the most tickets this queue issues in a service day."""
    recall_timeout_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Minutes a called patient has to arrive before a recall, then a no-show (Issue 43). ``None``
    uses the clinic's, then the platform default."""
    allows_remote_join: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    """Whether web, USSD and WhatsApp may join. ``False`` is walk-ins only, enforced server-side."""

    @property
    def kind_enum(self) -> QueueKind:
        """``kind`` as its enum member, for code that compares rather than renders."""
        return QueueKind(self.kind)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"Queue(id={self.id!r}, site_id={self.site_id!r}, slug={self.slug!r})"
