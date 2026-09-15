"""Finding a ticket at reception by its code: a scan or a few typed characters (Issue 70).

A patient shows the QR on their ticket page or stub, or reads out the code (``K7M-4QP``). A desk QR scanner
behaves as a keyboard: it types ``CLINICQ:K7M-4QP`` into the focused field and presses Enter. So the page is
one field that has focus and a plain ``GET`` form, with nothing to click: scan, and the ticket is on screen;
the field is empty and focused again for the next patient.

The page calls the same service as ``GET /api/v1/sites/{site}/tickets/lookup``
(:func:`src.modules.queue.ticket_codes.lookup_view`), so the rules are one: this clinic's tickets, today's
only, a code from an earlier day refused with the day it was for, an ended ticket's code used up, and a
transferred ticket's code opening the leg the visit is in now. It is gated on the front desk's read grant,
the grant that API checks.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from src.commons.exceptions import BKPropertyError
from src.commons.time import business_date
from src.core.nav_registry import destination
from src.database.session import get_db
from src.modules.queue.ticket_codes import LookupRefusal, lookup_view
from src.web.dashboard.routes import (
    ClinicPage,
    _visible,
    open_clinic_page,
    render_clinic_page,
)

router = APIRouter(tags=["web"])

#: The nav destination the page is gated on.
LOOKUP_KEY = "ticket_lookup"


@router.get("/dashboard/sites/{site_id}/lookup", response_class=HTMLResponse)
async def ticket_lookup_page(
    site_id: str,
    request: Request,
    db: Session = Depends(get_db),
    code: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    """The lookup field, and the ticket (or the reason there is none) for the code just scanned or typed."""
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key=LOOKUP_KEY,
        page_title=destination(LOOKUP_KEY).label,
        allowed=_visible(LOOKUP_KEY),
    )
    if not isinstance(opened, ClinicPage):
        return opened
    found = None
    refusal: str | None = None
    refusal_kind: str | None = None
    if code and code.strip():
        try:
            found = lookup_view(db, opened.access, code, today=business_date())
        except BKPropertyError as exc:
            refusal = str(exc)
            refusal_kind = exc.code.removeprefix("queue.lookup.")
    opened.context["lookup"] = {
        "code": (code or "").strip(),
        "found": found,
        "refusal": refusal,
        "refusal_kind": refusal_kind,
        "kinds": [kind.value for kind in LookupRefusal],
    }
    response = render_clinic_page(request, opened, "dashboard/lookup.html")
    response.headers["Cache-Control"] = "no-store"
    return response
