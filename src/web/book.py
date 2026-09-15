"""Booking an appointment from the web (Issue 81): ``/discover/clinics/{slug}/book``.

The page is the web's door to the booking service every channel calls. It signs the patient in with the join
page's phone sign-in (``patient/_sign_in.html``, ``patient-sign-in.js``), then ``patient-book.js`` works over
the JSON API: the day's bookable times (``GET /api/v1/clinics/{site}/appointments/availability``), booking,
the patient's own bookings, moving one to another time and cancelling. Every decision is the server's; the
page says the late rule in the words :mod:`src.modules.appointments.conversion` implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from src.commons.time import business_date, now_sast
from src.database.session import get_db
from src.modules.appointments import service
from src.modules.discovery import profile as profile_service
from src.modules.patients.sessions import signed_in_patient_id
from src.web.context import public_page_context
from src.web.discover import Slug
from src.web.join import NOTIFICATIONS_QUESTION, SignInView, sign_in_view
from src.web.routes import templates

router = APIRouter(prefix="/discover", include_in_schema=False)

DbSession = Annotated[Session, Depends(get_db)]

NO_STORE: Final = {"Cache-Control": "no-store"}
#: How many days the page offers to choose from, from today, capped by the clinic's horizon.
DAYS_OFFERED: Final = 14
#: The late rule, as a patient reads it (the rule itself is in src.modules.appointments.conversion).
LATE_RULE: Final = (
    "Your booking becomes your place in the queue about {lead} minutes before the time, whether or not you "
    "have arrived. If you are late you still have a place: if you are called and not there, you are called "
    "once more before the ticket is marked missed, and you can then join the queue again like anyone else."
)


@dataclass(frozen=True, slots=True)
class DayChoice:
    """One day the page offers."""

    iso: str
    label: str


@dataclass(frozen=True, slots=True)
class BookPage:
    """Everything the booking page renders, decided here."""

    site_id: str
    slug: str
    name: str
    clinic_href: str
    join_href: str
    signed_in: bool
    days: tuple[DayChoice, ...]
    late_rule: str
    sign_in: SignInView
    #: The messages question, word for word as every channel asks it (Issue 21).
    notifications_question: str


def _day_label(day: date, today: date) -> str:
    """``Today``, ``Tomorrow`` or ``Thu 8 Oct``."""
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%a} {day.day} {day:%b}"


@router.get("/clinics/{slug}/book", response_class=HTMLResponse, name="clinic_book")
def clinic_book(request: Request, slug: Slug, db: DbSession) -> HTMLResponse:
    """The booking page. A clinic a patient may not see is the same 404 as on the clinic page."""
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
    moment = now_sast()
    today = business_date(moment)
    policy, _ = service.policy_for(db, service.published_rows([found.site_id]))
    offered = min(DAYS_OFFERED, policy.horizon_days + 1)
    page = BookPage(
        site_id=found.site_id,
        slug=found.slug,
        name=found.name,
        clinic_href=f"/discover/clinics/{found.slug}",
        join_href=f"/discover/clinics/{found.slug}/join",
        signed_in=signed_in_patient_id(request, db) is not None,
        days=tuple(
            DayChoice(
                (today + timedelta(days=n)).isoformat(),
                _day_label(today + timedelta(days=n), today),
            )
            for n in range(offered)
        ),
        late_rule=LATE_RULE.format(lead=policy.convert_lead_minutes),
        sign_in=sign_in_view(),
        notifications_question=NOTIFICATIONS_QUESTION,
    )
    response = templates.TemplateResponse(
        request,
        "discover/book.html",
        public_page_context(
            request, page_title=f"Book a time at {found.name}", page=page
        ),
    )
    response.headers.update(NO_STORE)
    return response
