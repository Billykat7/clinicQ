"""Joining a clinic's queue from the web: ``/discover/clinics/{slug}/join`` (Issue 200).

The page the clinic page's **Join the queue** button opens (Issue 35). A patient signs in with their phone
number and a code, answers the notifications question, picks one of the clinic's queues that take remote
joins, and lands on their ticket page (Issue 68).

* **The page decides what to show, the API does the rest.** The route renders the clinic, the queues a
  phone may join, the notifications question and the step to start at, as :class:`JoinPage` (tested as
  data). ``patient-sign-in.js`` and ``patient-join.js`` then call the same endpoints every other client
  calls: ``/api/v1/patients/otp/request`` and ``/otp/verify`` (Issue 17),
  ``/api/v1/patients/me/consents/notifications`` (Issue 21) and
  ``/api/v1/clinics/{site_id}/queues/{queue_id}/tickets`` (Issue 40). Every refusal a patient reads is the
  API's own sentence, so the page cannot disagree with the USSD menu about why.
* **A signed-in patient starts at the queue.** Joining a queue they already hold answers with that ticket,
  and the page opens it.
* **When joining is not possible, the page says why and has no form:** joining from a phone is switched
  off (``PATIENT_JOIN_ENABLED``), the clinic is closed, or no queue takes remote joins. The reason is the
  one the clinic page's disabled button gives.
* **Never cached:** the page carries who is signed in.

The installed app's start page (``/t/``, :mod:`src.web.ticket`) offers the same sign-in, with the same
script, inside the app's scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import ConsentPurpose
from src.commons.phone import INVALID_PHONE_MESSAGE
from src.core.config import get_settings
from src.database.models.site import Site
from src.database.models.ticket import MAX_REASON_LENGTH
from src.database.session import get_db
from src.modules.appointments.call_forward import DEFAULT_TRAVEL_MINUTES, TRAVEL_CHOICES
from src.modules.discovery import profile as profile_service
from src.modules.discovery.profile import ClinicProfile
from src.modules.patients.consent import has_consent
from src.modules.patients.consent_text import CONSENT_WORDING
from src.modules.patients.schemas import PHONE_NOTICE
from src.modules.patients.sessions import signed_in_patient_id
from src.web.context import public_page_context
from src.web.discover import (
    JOIN_NOT_SWITCHED_ON,
    Slug,
    join_button,
    measured_queue_label,
    wait_label,
)
from src.web.routes import templates

router = APIRouter(prefix="/discover", include_in_schema=False)

DbSession = Annotated[Session, Depends(get_db)]

#: The page's HTML is never cached: it knows who is signed in.
NO_STORE: Final = {"Cache-Control": "no-store"}

#: The notifications question, word for word as every channel asks it (Issue 21).
NOTIFICATIONS_QUESTION: Final = CONSENT_WORDING[ConsentPurpose.NOTIFICATIONS]


class JoinStep(StrEnum):
    """Where the page starts. Each value is a ``data-step`` in ``discover/join.html``."""

    #: Nobody is signed in: ask for the phone number.
    PHONE = "phone"
    #: A patient is signed in: straight to choosing a queue.
    QUEUE = "queue"
    #: Joining is not possible here now: the reason, and no form.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SignInView:
    """The phone-number sign-in both the join page and the installed app's start page show."""

    #: Why the number is asked for, shown before it is typed: the words the code request answers with.
    phone_notice: str
    #: How many digits the code has (``OTP_LENGTH``), for the code field.
    code_length: int
    #: What a number that cannot be read is told. The API answers it for most wrong numbers, but one too
    #: short or too long is refused by request validation first, which has no sentence for a patient.
    invalid_phone: str


def sign_in_view() -> SignInView:
    """The sign-in as this deployment is configured."""
    return SignInView(
        phone_notice=PHONE_NOTICE,
        code_length=get_settings().otp_length,
        invalid_phone=INVALID_PHONE_MESSAGE,
    )


@dataclass(frozen=True, slots=True)
class JoinQueueOption:
    """One queue a phone may join, as the queue step offers it."""

    queue_id: str
    name: str
    waiting_label: str
    wait_label: str


@dataclass(frozen=True, slots=True)
class TravelChoice:
    """One answer to "how long does your trip to the clinic take?" (Issue 86)."""

    minutes: int
    label: str


def _travel_label(minutes: int) -> str:
    """``0`` is being there already; otherwise hours and minutes, as a person says them."""
    if minutes == 0:
        return "I am at the clinic already"
    hours, rest = divmod(minutes, 60)
    parts = [f"{hours} hour{'s' if hours > 1 else ''}"] if hours else []
    if rest:
        parts.append(f"{rest} minutes")
    return " ".join(parts)


#: The travel question's answers, offered only at a clinic with a virtual waiting room.
TRAVEL_OPTIONS: Final = tuple(
    TravelChoice(minutes, _travel_label(minutes)) for minutes in TRAVEL_CHOICES
)


@dataclass(frozen=True, slots=True)
class JoinPage:
    """Everything the join page renders, decided here."""

    site_id: str
    slug: str
    name: str
    clinic_href: str
    step: JoinStep
    #: Why joining is not possible, exactly when :attr:`step` is ``UNAVAILABLE``.
    unavailable_reason: str | None
    #: Only queues that take remote joins, in the clinic's own order.
    queues: tuple[JoinQueueOption, ...]
    signed_in: bool
    notifications_question: str
    #: The signed-in patient's current answer; ``False`` when nobody is signed in or they never said yes.
    notifications_granted: bool
    reason_max_length: int
    sign_in: SignInView
    #: The travel question's answers (Issue 86); empty, so no question, where the clinic has no virtual
    #: waiting room.
    travel_choices: tuple[TravelChoice, ...] = ()
    #: The answer selected to begin with: the default trip.
    travel_default: int = DEFAULT_TRAVEL_MINUTES


def join_page(
    profile: ClinicProfile,
    *,
    join_enabled: bool,
    signed_in: bool,
    notifications_granted: bool = False,
    virtual_waiting: bool = False,
) -> JoinPage:
    """The join page for this clinic now, for a visitor who is or is not signed in.

    ``virtual_waiting`` is the clinic's switch (Issue 86): only then is the patient asked how long their
    trip takes.
    """
    button = join_button(profile, join_enabled=join_enabled)
    options = tuple(
        JoinQueueOption(
            queue_id=queue.queue_id,
            name=queue.name,
            waiting_label=measured_queue_label(
                queue.waiting, queue.as_of, profile.evaluated_at
            ),
            wait_label=wait_label([queue.wait]),
        )
        for queue in profile.queues
        if queue.allows_remote_join
    )
    if not button.enabled:
        step, reason = JoinStep.UNAVAILABLE, button.reason or JOIN_NOT_SWITCHED_ON
    else:
        step, reason = (JoinStep.QUEUE if signed_in else JoinStep.PHONE), None
    return JoinPage(
        site_id=profile.site_id,
        slug=profile.slug,
        name=profile.name,
        clinic_href=f"/discover/clinics/{profile.slug}",
        step=step,
        unavailable_reason=reason,
        queues=options if step is not JoinStep.UNAVAILABLE else (),
        signed_in=signed_in,
        notifications_question=NOTIFICATIONS_QUESTION,
        notifications_granted=notifications_granted,
        reason_max_length=MAX_REASON_LENGTH,
        sign_in=sign_in_view(),
        travel_choices=TRAVEL_OPTIONS if virtual_waiting else (),
    )


@router.get("/clinics/{slug}/join", response_class=HTMLResponse, name="clinic_join")
def clinic_join(request: Request, slug: Slug, db: DbSession) -> HTMLResponse:
    """The join page. A clinic a patient may not see is the same 404 as on the clinic page."""
    found = profile_service.clinic_profile(db, slug)
    if found is None:
        response = templates.TemplateResponse(
            request,
            "discover/not_found.html",
            public_page_context(request, page_title="Find a clinic"),
            status_code=status.HTTP_404_NOT_FOUND,
        )
        response.headers.update(NO_STORE)
        return response
    patient_id = signed_in_patient_id(request, db)
    page = join_page(
        found,
        join_enabled=get_settings().patient_join_enabled,
        signed_in=patient_id is not None,
        notifications_granted=patient_id is not None
        and has_consent(db, patient_id, ConsentPurpose.NOTIFICATIONS),
        virtual_waiting=bool(
            db.execute(
                select(Site.virtual_waiting_enabled).where(Site.id == found.site_id)
            ).scalar_one_or_none()
        ),
    )
    response = templates.TemplateResponse(
        request,
        "discover/join.html",
        public_page_context(
            request, page_title=f"Join a queue at {found.name}", page=page
        ),
    )
    response.headers.update(NO_STORE)
    return response
