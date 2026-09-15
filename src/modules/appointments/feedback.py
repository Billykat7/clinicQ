"""Post-visit feedback: one question after a completed visit, one tap or keypress to answer (Issue 87).

Modelled on the NHS Friends and Family Test. The rules, and where each is held:

* **Exactly one request per completed visit, never repeated.** :func:`request_after_visit` runs inside the
  ``done`` transition (Issue 41), which a visit reaches once, on its last leg. Behind that,
  ``uq_visit_feedback_visit`` refuses a second row for the visit, and the notification's dedupe key
  (``<visit id>:feedback``) refuses a second message.
* **Sent through the notification service** (Issue 63), which applies the patient's consent to the survey
  (``ConsentPurpose.FEEDBACK_SURVEY``), their opt-out, a muted message and quiet hours. A request the
  service suppresses is recorded as ``suppressed``: it is not answerable and not counted.
* **One tap or one keypress on every channel.** An SMS (and, when Issue 75's adapter lands, a WhatsApp
  message) is answered by replying with one digit, 1 to 5; web push opens the answer page, and the ticket
  page shows the question to its own patient, where one tap on a score answers.
* **Free text is screened before it is stored**, with Issue 6's patterns plus e-mail addresses and ID
  numbers (:func:`src.core.log_redaction.screen_free_text`), so no report can show the raw words. It is
  kept only for the clinic's patient-text retention window (``site.reason_retention_days``, the same rule as
  the reason for a visit, until Issue 95's data map), then emptied by the nightly sweep.
* **The response rate is measured**: requests sent, answered, and the rate, per clinic, queue, staff member
  and channel (:func:`feedback_report`).
"""

from __future__ import annotations

import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    FeedbackChannel,
    FeedbackRequestStatus,
    NotificationStatus,
    PatientEvent,
)
from src.commons.exceptions import ConflictError, NotFoundError
from src.commons.time import business_day_bounds, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.core.log_redaction import screen_free_text
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import Queue, Site, Ticket, User, VisitFeedback
from src.database.models.feedback import FEEDBACK_SCORE_RANGE, MAX_FEEDBACK_COMMENT
from src.modules.appointments.feedback_schemas import FeedbackRequestOut, ScoreChoiceOut
from src.modules.notifications import service as notifications
from src.modules.patients.service import find_patient

#: The question, in the words every channel uses.
FEEDBACK_QUESTION: Final = "How was your visit today?"
#: What each score means, lowest first.
SCORE_LABELS: Final[dict[int, str]] = {
    1: "Very poor",
    2: "Poor",
    3: "Okay",
    4: "Good",
    5: "Very good",
}
#: How long after the visit a request can still be answered.
ANSWER_WINDOW_DAYS: Final = 7
#: Where a request is answered in a browser.
ANSWER_PAGE_PREFIX: Final = "/f/"
#: A token is 256 random bits, URL-safe: 43 characters.
_TOKEN_SHAPE: Final = re.compile(r"[A-Za-z0-9_-]{43}")
#: What an SMS or WhatsApp reply must start with to be an answer: one digit on the scale.
_REPLY_SCORE: Final = re.compile(
    rf"^\s*([{FEEDBACK_SCORE_RANGE[0]}-{FEEDBACK_SCORE_RANGE[1]}])(?:[\s.,!:;-]+(.*))?$",
    re.DOTALL,
)
#: The refusal codes.
NOT_FOUND_CODE: Final = "feedback.not_found"
EXPIRED_CODE: Final = "feedback.expired"
ANSWERED_CODE: Final = "feedback.already_answered"
#: Said beside a per-staff breakdown, so it is read as a service measure, not a league table.
STAFF_NOTE: Final = (
    "Scores follow the visits each person marked done. They are for noticing what patients value, "
    "not for ranking colleagues, and a small number of answers says very little."
)


class FeedbackAnsweredError(ConflictError):
    """This request has been answered already: HTTP 409 ``feedback.already_answered``."""


class FeedbackExpiredError(ConflictError):
    """This request is past its answer window: HTTP 409 ``feedback.expired``."""


def answer_path(token: str) -> str:
    """The answer page for one request."""
    return f"{ANSWER_PAGE_PREFIX}{token}"


# --------------------------------------------------------------------------------------
# Asking
# --------------------------------------------------------------------------------------


def request_after_visit(
    db: Session, ticket: Ticket, *, served_by: str | None, moment: datetime
) -> VisitFeedback | None:
    """Ask the patient how the visit went, once. Called by the ``done`` transition; the caller commits.

    ``None`` for a walk-in with no patient, or when the visit was already asked about.
    """
    if ticket.patient_id is None:
        return None
    if db.execute(
        select(VisitFeedback.id).where(VisitFeedback.visit_id == ticket.visit_id)
    ).first():
        return None
    site = db.get(Site, ticket.site_id)
    queue = db.get(Queue, ticket.queue_id)
    if site is None or queue is None:
        return None
    row = VisitFeedback(
        site_id=ticket.site_id,
        queue_id=ticket.queue_id,
        visit_id=ticket.visit_id,
        ticket_id=ticket.id,
        patient_id=ticket.patient_id,
        served_by=served_by,
        token=secrets.token_urlsafe(32),
        request_status=FeedbackRequestStatus.SUPPRESSED.value,
        requested_at=moment,
        expires_at=moment + timedelta(days=ANSWER_WINDOW_DAYS),
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        return None  # another transaction asked about this visit in the same instant
    sent = notifications.notify(
        db,
        patient_id=ticket.patient_id,
        event=PatientEvent.FEEDBACK,
        context={
            "number": ticket.number,
            "clinic": site.name,
            "queue": queue.name,
            "room": queue.room_label,
            "page_url": answer_path(row.token),
        },
        site_id=ticket.site_id,
        dedupe_key=f"{ticket.visit_id}:feedback",
        now=moment,
    )
    if sent is not None and sent.status != NotificationStatus.SUPPRESSED.value:
        row.request_status = FeedbackRequestStatus.SENT.value
    db.flush()
    return row


# --------------------------------------------------------------------------------------
# Answering
# --------------------------------------------------------------------------------------


def request_view(
    db: Session, row: VisitFeedback, *, moment: datetime | None = None
) -> FeedbackRequestOut:
    """A request as the answer page and the patient's ticket page show it: no patient, no ticket."""
    moment = moment or now_sast()
    site = db.get(Site, row.site_id)
    expired = moment >= stored_sast(row.expires_at)
    answered = row.answered_at is not None
    return FeedbackRequestOut(
        clinic=site.name if site is not None else "",
        question=FEEDBACK_QUESTION,
        choices=[
            ScoreChoiceOut(score=score, label=label)
            for score, label in SCORE_LABELS.items()
        ],
        answered=answered,
        score=row.score,
        expired=expired,
        answer_url=None if answered or expired else f"/api/v1/feedback/{row.token}",
        max_comment_length=MAX_FEEDBACK_COMMENT,
    )


def sent_for_ticket(db: Session, ticket_id: str) -> VisitFeedback | None:
    """The request sent after the visit that ended with this ticket, if one was sent."""
    return db.execute(
        select(VisitFeedback).where(
            VisitFeedback.ticket_id == ticket_id,
            VisitFeedback.request_status == FeedbackRequestStatus.SENT.value,
        )
    ).scalar_one_or_none()


def find_by_token(db: Session, token: str) -> VisitFeedback | None:
    """The request a link names, or ``None``. A token of the wrong shape is not even looked up."""
    if not _TOKEN_SHAPE.fullmatch(token):
        return None
    return db.execute(
        select(VisitFeedback).where(VisitFeedback.token == token)
    ).scalar_one_or_none()


def answerable(db: Session, token: str) -> VisitFeedback:
    """The request a link names, if it was sent. One that was suppressed is the same "not found".

    Raises:
        NotFoundError: No such request, or it was never sent.
    """
    row = find_by_token(db, token)
    if row is None or row.request_status != FeedbackRequestStatus.SENT.value:
        raise NotFoundError("No such feedback request.", code=NOT_FOUND_CODE)
    return row


def answer(
    db: Session,
    row: VisitFeedback,
    *,
    score: int,
    comment: str | None,
    channel: FeedbackChannel,
    moment: datetime | None = None,
) -> VisitFeedback:
    """Record the patient's answer, once: the score, and the comment after screening. The caller commits.

    Raises:
        FeedbackAnsweredError: Already answered; the first answer stands.
        FeedbackExpiredError: Past :data:`ANSWER_WINDOW_DAYS`.
        ValueError: A score off the scale; the API validates first, so this is a programming error.
    """
    moment = moment or now_sast()
    if not FEEDBACK_SCORE_RANGE[0] <= score <= FEEDBACK_SCORE_RANGE[1]:
        raise ValueError(f"A score is 1 to 5, not {score}.")
    if row.answered_at is not None:
        raise FeedbackAnsweredError(
            "Thank you, this visit has been rated already.", code=ANSWERED_CODE
        )
    if moment >= stored_sast(row.expires_at):
        raise FeedbackExpiredError(
            "This question has closed. Thank you all the same.", code=EXPIRED_CODE
        )
    words = (comment or "").strip()[:MAX_FEEDBACK_COMMENT]
    screened, removed = screen_free_text(words) if words else (None, 0)
    site = db.get(Site, row.site_id)
    row.score = score
    row.comment = screened
    row.comment_redactions = removed
    row.answered_at = moment
    row.answered_via = channel.value
    row.comment_expires_at = (
        moment + timedelta(days=site.reason_retention_days)
        if screened and site is not None
        else None
    )
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.VISIT_FEEDBACK,
        entity_id=row.id,
        actor=f"patient:{row.patient_id}",
        site_id=row.site_id,
        # The score and the channel, never the words.
        context=f"feedback {score} via {channel.value}"
        + (", with a comment" if screened else ""),
    )
    db.flush()
    return row


def answer_by_reply(
    db: Session,
    phone: str,
    text: str,
    *,
    channel: FeedbackChannel,
    moment: datetime | None = None,
) -> str | None:
    """Treat a message reply starting with a digit from 1 to 5 as the answer to the patient's open request.

    Returns the patient's id when it answered one, ``None`` when the reply is not an answer, the number is
    unknown, or the patient has no open request. Anything after the digit is the comment. Answered, closed
    and suppressed requests are skipped, so a stray "5" weeks later changes nothing.
    """
    moment = moment or now_sast()
    matched = _REPLY_SCORE.match(text)
    if matched is None:
        return None
    patient = find_patient(db, phone)
    if patient is None:
        return None
    row = db.execute(
        select(VisitFeedback)
        .where(
            VisitFeedback.patient_id == patient.id,
            VisitFeedback.request_status == FeedbackRequestStatus.SENT.value,
            VisitFeedback.answered_at.is_(None),
            VisitFeedback.expires_at > moment,
        )
        .order_by(VisitFeedback.requested_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    answer(
        db,
        row,
        score=int(matched.group(1)),
        comment=matched.group(2),
        channel=channel,
        moment=moment,
    )
    return patient.id


# --------------------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------------------


def purge_expired_comments(db: Session, *, moment: datetime | None = None) -> int:
    """Empty every feedback comment past its retention; return how many. The scores stay. The caller commits.

    One audit row with the count, never the words, like the visit note sweep (Issue 53).
    """
    moment = moment or now_sast()
    result = db.execute(
        update(VisitFeedback)
        .where(
            VisitFeedback.comment.is_not(None),
            VisitFeedback.comment_expires_at <= moment,
        )
        .values(comment=None)
        .execution_options(synchronize_session=False)
    )
    purged = int(getattr(result, "rowcount", 0) or 0)
    if purged:
        record_audit_event(
            db,
            action=AuditAction.DELETE,
            entity_type=AuditEntityType.VISIT_FEEDBACK,
            entity_id="retention-sweep",
            actor="system:feedback-comment-retention",
            context=f"emptied {purged} feedback comment(s) past their retention",
        )
    return purged


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tally:
    """Requests sent and answered, and the scores given, for one slice of the report."""

    sent: int
    answered: int
    scores: tuple[int, ...]

    @property
    def response_rate(self) -> float | None:
        """Answered over sent, to three places; ``None`` when nothing was sent."""
        return round(self.answered / self.sent, 3) if self.sent else None

    @property
    def average_score(self) -> float | None:
        """The mean score, to two places; ``None`` with no answers."""
        return round(sum(self.scores) / len(self.scores), 2) if self.scores else None


@dataclass(frozen=True, slots=True)
class SliceTally:
    """A named slice of the report: a queue, or a staff member."""

    key: str | None
    name: str
    tally: Tally


@dataclass(frozen=True, slots=True)
class CommentLine:
    """One screened comment as a report shows it: no patient, no ticket number."""

    answered_at: datetime
    queue_name: str
    score: int
    comment: str


@dataclass(frozen=True, slots=True)
class FeedbackReport:
    """One clinic's feedback over a range of service days."""

    site_id: str
    start: date
    end: date
    overall: Tally
    #: How many answers gave each score, 1 to 5.
    distribution: dict[int, int]
    #: Requests not sent (no consent to the survey, an opt-out, a muted message): outside the rate.
    suppressed: int
    by_queue: tuple[SliceTally, ...]
    by_staff: tuple[SliceTally, ...]
    #: How many answers came through each channel.
    answers_by_channel: dict[str, int]
    comments: tuple[CommentLine, ...]


#: How many screened comments a report lists, newest first.
REPORT_COMMENTS: Final = 50


def _tally(rows: list[VisitFeedback]) -> Tally:
    """Count one slice's sent and answered requests and gather its scores."""
    sent = [
        row for row in rows if row.request_status == FeedbackRequestStatus.SENT.value
    ]
    scores = tuple(row.score for row in sent if row.score is not None)
    return Tally(sent=len(sent), answered=len(scores), scores=scores)


def feedback_report(
    db: Session, access: SiteAccess, *, start: date, end: date
) -> FeedbackReport:
    """Feedback at one clinic for requests made from ``start`` to ``end`` (Johannesburg service days).

    Per clinic, per queue and per staff member who marked the visit done, each with its response rate and
    average score; the score distribution; answers per channel; and the newest screened comments. Read
    through the site guard. What M12's reports (Issues 89 and 94) build their screens on.
    """
    first, _ = business_day_bounds(start)
    _, last = business_day_bounds(end)
    rows = list(
        db.execute(
            scoped_select(VisitFeedback, access)
            .where(
                VisitFeedback.requested_at >= first, VisitFeedback.requested_at < last
            )
            .order_by(VisitFeedback.requested_at)
        ).scalars()
    )
    queue_names = {
        queue.id: queue.name
        for queue in db.execute(scoped_select(Queue, access)).scalars()
    }
    by_queue: dict[str, list[VisitFeedback]] = defaultdict(list)
    by_staff: dict[str | None, list[VisitFeedback]] = defaultdict(list)
    for row in rows:
        by_queue[row.queue_id].append(row)
        by_staff[row.served_by].append(row)
    staff_names: dict[str | None, str] = {None: "Not recorded"}
    for user_id in by_staff:
        user = db.get(User, user_id) if user_id is not None else None
        if user is not None:
            staff_names[user_id] = user.email
    overall = _tally(rows)
    answered = [
        (stored_sast(row.answered_at), row)
        for row in rows
        if row.answered_at is not None and row.score is not None
    ]
    comments = [
        CommentLine(
            answered_at=at,
            queue_name=queue_names.get(row.queue_id, "Removed queue"),
            score=row.score,
            comment=row.comment,
        )
        for at, row in sorted(answered, key=lambda pair: pair[0], reverse=True)
        if row.comment is not None and row.score is not None
    ][:REPORT_COMMENTS]
    return FeedbackReport(
        site_id=access.site_id,
        start=start,
        end=end,
        overall=overall,
        distribution={score: Counter(overall.scores)[score] for score in SCORE_LABELS},
        suppressed=sum(
            row.request_status == FeedbackRequestStatus.SUPPRESSED.value for row in rows
        ),
        by_queue=tuple(
            SliceTally(
                queue_id, queue_names.get(queue_id, "Removed queue"), _tally(items)
            )
            for queue_id, items in sorted(
                by_queue.items(), key=lambda item: queue_names.get(item[0], "")
            )
        ),
        by_staff=tuple(
            sorted(
                (
                    SliceTally(
                        user_id, staff_names.get(user_id, "Not recorded"), _tally(items)
                    )
                    for user_id, items in by_staff.items()
                ),
                key=lambda item: item.name,
            )
        ),
        answers_by_channel=dict(
            sorted(
                Counter(
                    row.answered_via for _, row in answered if row.answered_via
                ).items()
            )
        ),
        comments=tuple(comments),
    )
