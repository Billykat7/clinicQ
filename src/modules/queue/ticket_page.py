"""The patient's ticket page: one ticket, found by its unguessable link, as it stands now (Issue 68).

The page answers "how much longer?" for someone deciding whether to leave the house, so everything it
shows is derived on every read from the queue as it is, never stored: the place in line, the wait
range, and the headline (:class:`~src.commons.enums.TicketPageHeadline`) that says what to do.

Three rules shape it:

* **The link is the only way in, and it is not an id.** :func:`find_by_page_token` accepts exactly the
  shape :func:`~src.modules.queue.sequence.new_page_token` produces (43 URL-safe characters, 256 random
  bits) and looks nothing else up: a ticket id, a number or a reference code finds nothing.
* **Following is not owning.** Anyone with the link may follow the ticket, because that is what sharing
  it is for. Only the ticket's own patient, signed in on this browser, is offered cancellation
  (``cancel_url``), and the cancel route checks that again.
* **Nothing about the patient.** No name, phone number or reason is in the page's data: a link
  forwarded one time too many shows a number, a clinic and a place in line.
"""

import re
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import TICKET_TERMINAL_STATUSES, TicketPageHeadline, TicketStatus
from src.commons.time import now_sast, stored_sast
from src.core.config import get_settings
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import PAGE_TOKEN_LENGTH, Ticket
from src.modules.queue.schemas import (
    TicketPageClinic,
    TicketPageOut,
    TicketPageQueue,
    WaitOut,
)
from src.modules.queue.sequence import format_reference_code
from src.modules.queue.tickets import waiting_ahead
from src.modules.queue.waits import estimates_for

#: What a page token looks like: nothing else is looked up.
PAGE_TOKEN_SHAPE: Final = re.compile(rf"[A-Za-z0-9_-]{{{PAGE_TOKEN_LENGTH}}}")
#: Where a browser's web push subscription is sent (Issue 64).
PUSH_SUBSCRIBE_URL: Final = "/api/v1/notifications/web-push/subscriptions"
#: How often the page reads its state again while the live stream is down.
REFRESH_SECONDS: Final = 15
#: How long without word from the server (a state or a heartbeat) before the page says it is not live:
#: three missed heartbeats of the stream's 15 seconds.
STALE_AFTER_SECONDS: Final = 45

_HEADLINE_BY_STATUS: Final[dict[TicketStatus, TicketPageHeadline]] = {
    TicketStatus.CALLED: TicketPageHeadline.CALLED,
    TicketStatus.RECALLED: TicketPageHeadline.CALLED,
    TicketStatus.IN_PROGRESS: TicketPageHeadline.IN_PROGRESS,
    TicketStatus.DONE: TicketPageHeadline.DONE,
    TicketStatus.CANCELLED: TicketPageHeadline.CANCELLED,
    TicketStatus.NO_SHOW: TicketPageHeadline.MISSED,
    TicketStatus.TRANSFERRED: TicketPageHeadline.TRANSFERRED,
}


def page_path(token: str) -> str:
    """The page's path for a token."""
    return f"/t/{token}"


def page_url_for(ticket: Ticket) -> str | None:
    """The ticket's page, or ``None`` for a ticket issued before pages existed."""
    return page_path(ticket.page_token) if ticket.page_token else None


def find_by_page_token(db: Session, token: str) -> Ticket | None:
    """The ticket this link belongs to, or ``None``. Anything not shaped like a token finds nothing."""
    if not PAGE_TOKEN_SHAPE.fullmatch(token):
        return None
    return db.execute(
        select(Ticket).where(Ticket.page_token == token)
    ).scalar_one_or_none()


def _address(site: Site) -> str:
    """The clinic's address on one line."""
    return ", ".join(
        part for part in (site.address_line, site.suburb, site.city) if part
    )


def _next_leg(db: Session, ticket: Ticket) -> Ticket | None:
    """The ticket a transfer issued from ``ticket``, when there is one."""
    return db.execute(
        select(Ticket).where(Ticket.transferred_from_id == ticket.id)
    ).scalar_one_or_none()


def page_state(
    db: Session,
    ticket: Ticket,
    *,
    viewer_patient_id: str | None,
    moment: datetime | None = None,
) -> TicketPageOut:
    """The ticket page's data for ``ticket`` as it stands at ``moment``.

    Args:
        db: A session.
        ticket: The ticket, already found by its page token.
        viewer_patient_id: The patient signed in on the browser asking, if any; only the ticket's own
            patient is offered cancellation.
        moment: When (aware); ``None`` means now in Johannesburg.
    """
    moment = moment or now_sast()
    queue = db.get(Queue, ticket.queue_id)
    site = db.get(Site, ticket.site_id)
    if (
        queue is None or site is None
    ):  # a ticket's queue and clinic are never deleted (RESTRICT)
        raise LookupError(f"ticket {ticket.id} has lost its queue or clinic")
    status = ticket.status_enum
    ahead: int | None = None
    wait: WaitOut | None = None
    if status is TicketStatus.WAITING:
        ahead = waiting_ahead(db, ticket)
        wait = WaitOut.of(
            estimates_for(db, [queue], {queue.id: ahead}, moment=moment)[queue.id]
        )
        headline = TicketPageHeadline.NEXT if ahead == 0 else TicketPageHeadline.WAITING
    else:
        headline = _HEADLINE_BY_STATUS[status]
    next_leg = _next_leg(db, ticket) if status is TicketStatus.TRANSFERRED else None
    owner = viewer_patient_id is not None and viewer_patient_id == ticket.patient_id
    location = site.location
    settings = get_settings()
    # Offered only after joining (this page exists only for a ticket), only to the patient themselves,
    # and never asked for on load: the page shows a button, and the browser asks when it is pressed.
    offer_push = (
        owner and settings.web_push_enabled and status not in TICKET_TERMINAL_STATUSES
    )
    return TicketPageOut(
        number=ticket.number,
        reference_code=format_reference_code(ticket.reference_code),
        headline=headline,
        status=status,
        service_day=ticket.service_day,
        clinic=TicketPageClinic(
            name=site.name,
            address=_address(site),
            phone_e164=site.phone_e164,
            call_url=f"tel:{site.phone_e164}" if site.phone_e164 else None,
            directions_url=(
                "https://www.google.com/maps/dir/?api=1&destination="
                f"{location.latitude:.6f},{location.longitude:.6f}"
            ),
        ),
        queue=TicketPageQueue(name=queue.name, room=queue.room_label),
        position=None if ahead is None else ahead + 1,
        waiting_ahead=ahead,
        wait=wait,
        called_at=stored_sast(called)
        if (called := ticket.recalled_at or ticket.called_at)
        else None,
        as_of=moment,
        refresh_seconds=REFRESH_SECONDS,
        stale_after_seconds=STALE_AFTER_SECONDS,
        stream_url=(
            f"{page_path(ticket.page_token)}/stream"
            if ticket.page_token and status not in TICKET_TERMINAL_STATUSES
            else None
        ),
        cancel_url=(
            f"/api/v1/patients/me/tickets/{ticket.id}/cancel"
            if owner and status is TicketStatus.WAITING
            else None
        ),
        next_page_url=page_url_for(next_leg) if next_leg is not None else None,
        push_key=settings.web_push_vapid_public_key if offer_push else None,
        push_subscribe_url=PUSH_SUBSCRIBE_URL if offer_push else None,
        preferences_url=(
            f"/api/v1/notifications/patient-preferences/{ticket.page_token}"
            if ticket.patient_id and ticket.page_token
            else None
        ),
    )
