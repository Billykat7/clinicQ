"""Finding a ticket at reception by its QR or code, in a real browser (Issue 70).

A desk QR scanner is a keyboard: it types what the QR says and presses Enter. So "scanning" here is typing
the QR's payload into the focused field and pressing Enter, exactly what the scanner sends; the payload is
the one the patient's ticket page draws. What is shown:

* **the field has focus when the page opens**, so a scan needs no click, and the ticket opens on Enter;
* **after a result the field is empty and focused again**, ready for the next patient;
* **a code from yesterday says which day it was for.**

Set ``TICKET_PAGE_SHOTS`` to a folder to save the screenshots quoted in the pull request.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from src.commons.enums import TicketSource
from src.commons.time import business_date
from src.database.models import Queue, Ticket
from src.modules.queue.sequence import issue_ticket
from src.modules.queue.ticket_codes import qr_for, qr_payload

pytestmark = pytest.mark.postgres

SHOTS = os.environ.get("TICKET_PAGE_SHOTS", "")


def _issue(clinic: SimpleNamespace, name: str) -> Ticket:
    with clinic.session() as db:
        ticket = issue_ticket(
            db,
            queue=db.get_one(Queue, clinic.triage),
            source=TicketSource.WALK_IN,
            walk_in_name=name,
        )
        db.commit()
        return ticket


def _shot(page: Any, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def test_a_scan_opens_the_ticket_with_no_click_and_the_field_is_ready_for_the_next(
    fresh_day: SimpleNamespace, signed_in: Callable[..., Any]
) -> None:
    """How to verify, step 1, as the desk scanner sends it."""
    _issue(fresh_day, "Ahead")
    mine = _issue(fresh_day, "Thandi")
    page = signed_in("desk", f"/dashboard/sites/{fresh_day.site}/lookup")
    assert page.evaluate("() => document.activeElement.id") == "lookup-code"

    page.keyboard.type(qr_payload(mine.reference_code))
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-lookup='found']")
    assert page.locator("#lookup-number").inner_text() == mine.number
    assert (
        page.locator(".lookup-message").inner_text()
        == f"{mine.number} is waiting in Triage, 1 ahead."
    )
    assert page.evaluate(
        "() => [document.activeElement.id, document.activeElement.value]"
    ) == ["lookup-code", ""]
    _shot(page, "reception-lookup-found")

    # The printed stub carries the same code: the QR the scan just read, and the code to say.
    page.goto(f"/dashboard/sites/{fresh_day.site}/walk-in/tickets/{mine.id}/stub")
    drawn = page.locator(".stub-qr path").get_attribute("d")
    assert drawn == qr_for(mine.reference_code).path
    assert (
        page.locator(".stub-ref strong").inner_text()
        == f"{mine.reference_code[:3]}-{mine.reference_code[3:]}"
    )
    if SHOTS:
        page.locator(".stub").screenshot(path=str(Path(SHOTS) / "stub-with-qr.png"))


def test_a_code_from_yesterday_says_which_day_it_was_for(
    fresh_day: SimpleNamespace, signed_in: Callable[..., Any]
) -> None:
    """How to verify, step 3."""
    old = _issue(fresh_day, "Yesterday")
    yesterday = business_date() - timedelta(days=1)
    with fresh_day.session() as db:
        db.execute(
            update(Ticket).where(Ticket.id == old.id).values(service_day=yesterday)
        )
        db.commit()
    code = f"{old.reference_code[:3]}-{old.reference_code[3:]}"
    page = signed_in("desk", f"/dashboard/sites/{fresh_day.site}/lookup")
    page.keyboard.type(code.lower())
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-lookup='expired']")
    assert page.locator("[data-lookup='expired'] [role='alert']").inner_text() == (
        f"Code {code} was for {yesterday:%A %d %B %Y}. A ticket code works only on the day it was issued: "
        "the patient needs a new ticket today."
    )
    _shot(page, "reception-lookup-yesterday")
