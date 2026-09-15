"""Request and response models for post-visit feedback and its report (Issue 87)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.database.models.feedback import FEEDBACK_SCORE_RANGE, MAX_FEEDBACK_COMMENT


class ScoreChoiceOut(BaseModel):
    """One answer on the scale."""

    score: int = Field(ge=FEEDBACK_SCORE_RANGE[0], le=FEEDBACK_SCORE_RANGE[1])
    label: str


class FeedbackRequestOut(BaseModel):
    """A feedback request as its answer page shows it: nothing about the patient or the ticket."""

    clinic: str
    question: str
    choices: list[ScoreChoiceOut]
    answered: bool
    #: The score given, once answered.
    score: int | None
    expired: bool
    #: Where the answer is sent; ``None`` once answered or closed.
    answer_url: str | None
    max_comment_length: int


class FeedbackAnswerIn(BaseModel):
    """An answer: the score, and optionally a comment (screened before it is stored)."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=FEEDBACK_SCORE_RANGE[0], le=FEEDBACK_SCORE_RANGE[1])
    comment: str | None = Field(default=None, max_length=MAX_FEEDBACK_COMMENT)


class TallyOut(BaseModel):
    """Requests sent and answered, the response rate and the average score."""

    sent: int = Field(ge=0)
    answered: int = Field(ge=0)
    response_rate: float | None = Field(
        description="Answered over sent; null when nothing was sent."
    )
    average_score: float | None = Field(description="Null with no answers.")


class SliceOut(BaseModel):
    """A queue's, or a staff member's, share of the report."""

    id: str | None
    name: str
    tally: TallyOut


class CommentOut(BaseModel):
    """One screened comment: when, which queue, the score. Never the patient or the ticket."""

    answered_at: datetime
    queue_name: str
    score: int
    comment: str


class FeedbackReportOut(BaseModel):
    """A clinic's post-visit feedback over a range of service days."""

    site_id: str
    start: date
    end: date
    overall: TallyOut
    distribution: dict[str, int] = Field(
        description="Answers per score, keyed '1' to '5'."
    )
    suppressed: int = Field(
        ge=0,
        description="Requests not sent (no consent, an opt-out, a muted message); outside the rate.",
    )
    by_queue: list[SliceOut]
    by_staff: list[SliceOut]
    staff_note: str
    answers_by_channel: dict[str, int]
    comments: list[CommentOut]
