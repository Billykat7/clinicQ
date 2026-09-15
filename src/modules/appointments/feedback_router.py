"""HTTP routes for post-visit feedback: the one-tap answer, and a clinic's report (Issue 87).

* ``GET``/``POST /feedback/{token}`` answer a request by its link, with **no sign-in**: the link is the
  secret, 256 random bits sent only to the patient, like a ticket page's. A request that was suppressed
  (never sent) is the same 404 as a made-up token. These are on the API route guard's public list.
* ``GET /sites/{site_id}/reports/feedback`` is the clinic's report, behind ``sites.reports:read``, the grant
  every clinic report uses.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from src.commons.enums import FeedbackChannel
from src.commons.time import business_date, now_sast
from src.core.site_scope import SiteAccess, require_site_access
from src.database.session import get_db
from src.modules.appointments import feedback
from src.modules.appointments.feedback_schemas import (
    CommentOut,
    FeedbackAnswerIn,
    FeedbackReportOut,
    FeedbackRequestOut,
    SliceOut,
    TallyOut,
)
from src.modules.discovery.analytics import DEFAULT_REPORT_DAYS, MAX_REPORT_DAYS

router = APIRouter(tags=["feedback"])

DbSession = Annotated[Session, Depends(get_db)]
FeedbackReportsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.reports", "read"))
]


@router.get(
    "/feedback/{token}",
    response_model=FeedbackRequestOut,
    operation_id="feedbackRead",
    summary="A post-visit question, by its link",
)
def read_request(token: str, response: Response, db: DbSession) -> FeedbackRequestOut:
    """The question, the choices, and whether it has been answered or has closed."""
    response.headers["Cache-Control"] = "no-store"
    return feedback.request_view(db, feedback.answerable(db, token))


@router.post(
    "/feedback/{token}",
    response_model=FeedbackRequestOut,
    operation_id="feedbackAnswer",
    summary="Answer a post-visit question: one tap",
)
def answer_request(
    token: str, payload: FeedbackAnswerIn, response: Response, db: DbSession
) -> FeedbackRequestOut:
    """Record the score (and any comment, screened for personal information first). Answered once.

    409 ``feedback.already_answered`` for a second answer (the first stands), 409 ``feedback.expired`` after
    the answer window.
    """
    row = feedback.answerable(db, token)
    feedback.answer(
        db,
        row,
        score=payload.score,
        comment=payload.comment,
        channel=FeedbackChannel.WEB,
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return feedback.request_view(db, row)


@router.get(
    "/sites/{site_id}/reports/feedback",
    response_model=FeedbackReportOut,
    operation_id="feedbackReport",
    summary="How patients rated their visits, and how many answered",
)
def feedback_report(
    access: FeedbackReportsRead,
    db: DbSession,
    start: Annotated[
        date | None, Query(description="First service day, inclusive.")
    ] = None,
    end: Annotated[
        date | None, Query(description="Last service day, inclusive.")
    ] = None,
) -> FeedbackReportOut:
    """Scores and response rates per clinic, queue and staff member; the last 30 days by default."""
    last = end or business_date(now_sast())
    first = start or last - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    if first > last:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The start comes after the end."
        )
    if (last - first).days + 1 > MAX_REPORT_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A report covers at most {MAX_REPORT_DAYS} days.",
        )
    report = feedback.feedback_report(db, access, start=first, end=last)

    def tally(found: feedback.Tally) -> TallyOut:
        return TallyOut(
            sent=found.sent,
            answered=found.answered,
            response_rate=found.response_rate,
            average_score=found.average_score,
        )

    return FeedbackReportOut(
        site_id=report.site_id,
        start=report.start,
        end=report.end,
        overall=tally(report.overall),
        distribution={
            str(score): count for score, count in report.distribution.items()
        },
        suppressed=report.suppressed,
        by_queue=[
            SliceOut(id=item.key, name=item.name, tally=tally(item.tally))
            for item in report.by_queue
        ],
        by_staff=[
            SliceOut(id=item.key, name=item.name, tally=tally(item.tally))
            for item in report.by_staff
        ],
        staff_note=feedback.STAFF_NOTE,
        answers_by_channel=report.answers_by_channel,
        comments=[
            CommentOut(
                answered_at=line.answered_at,
                queue_name=line.queue_name,
                score=line.score,
                comment=line.comment,
            )
            for line in report.comments
        ],
    )
