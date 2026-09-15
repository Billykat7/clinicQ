"""One post-visit feedback request per completed visit, and its answer (Issue 87).

Modelled on the NHS Friends and Family Test: one question, answered with one tap or one keypress, after
the visit. The table's shape is its rules:

* **``visit_id`` is unique.** A visit is asked once, whatever sends the request twice, and a transfer's
  several tickets are still one visit (Issue 45).
* **``token`` is the answer link's secret**, 256 random bits, so a request is answered by the patient it
  was sent to without signing in, and one link says nothing about another.
* **``request_status``** says whether the request was really sent or suppressed (no consent, an opt-out,
  a muted message), so the response rate divides by requests that reached somebody.
* **``comment`` is stored already screened** for personal information (phone numbers, e-mail addresses,
  ID numbers), never raw, and is emptied at ``comment_expires_at``, the clinic's patient-text retention
  (Issue 27, until Issue 95's data map). The score stays: it is not personal text.
* **``served_by``** is the staff member who marked the visit done, for a per-staff view where one is
  wanted; ``SET NULL`` when the account is removed, like the audit trail.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: The scale: 1 is very poor and 5 is very good.
FEEDBACK_SCORE_RANGE = (1, 5)
#: The longest comment kept, after screening. A sentence or two, as an SMS reply can carry.
MAX_FEEDBACK_COMMENT = 500


class VisitFeedback(Base, TimestampMixin):
    """The feedback request sent after one completed visit, and the patient's answer if they gave one."""

    __tablename__ = "visit_feedback"
    __table_args__ = (
        UniqueConstraint("visit_id", name="uq_visit_feedback_visit"),
        UniqueConstraint("token", name="uq_visit_feedback_token"),
        CheckConstraint(
            f"score IS NULL OR score BETWEEN {FEEDBACK_SCORE_RANGE[0]} AND {FEEDBACK_SCORE_RANGE[1]}",
            name="score_range",
        ),
        CheckConstraint(
            "(score IS NULL) = (answered_at IS NULL)", name="answered_with_score"
        ),
        CheckConstraint(
            "comment IS NULL OR answered_at IS NOT NULL",
            name="comment_only_when_answered",
        ),
        # The reports read a clinic's requests over a range of days.
        Index("ix_clinicq_visit_feedback_site_requested", "site_id", "requested_at"),
        # An SMS reply finds the patient's newest open request.
        Index(
            "ix_clinicq_visit_feedback_patient_requested", "patient_id", "requested_at"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    """The queue the visit ended in: the last leg's."""
    visit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.visit.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.ticket.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The ticket that was marked done."""
    patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    served_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.user.id", ondelete="SET NULL"), nullable=True
    )
    token: Mapped[str] = mapped_column(String(43), nullable=False)
    request_status: Mapped[str] = mapped_column(String(16), nullable=False)
    """:class:`~src.commons.enums.FeedbackRequestStatus`."""
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """After this the request can no longer be answered."""
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    """The patient's words after screening; emptied at :attr:`comment_expires_at`."""
    comment_redactions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """How many pieces of personal information screening removed from the comment."""
    comment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answered_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """:class:`~src.commons.enums.FeedbackChannel` the answer came through."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures; never the comment."""
        return (
            f"VisitFeedback(visit_id={self.visit_id!r}, status={self.request_status!r}, "
            f"score={self.score})"
        )
