"""Message settings on the ticket page, in a real browser (Issue 67).

The page's own form and script against the real preferences route and database:

* **a phone that was only sent the link can change how the patient is told**: quiet hours and a muted
  message are saved, and the server stores them;
* **stopping all messages from the page is stored as an opt-out**, the same one an SMS STOP reply makes;
* **half a quiet-hours window is refused on the page** before anything is sent;
* **a walk-in with no patient has no settings to change**, so the panel is not shown;
* **the panel fits a 320 px screen.**

Set ``TICKET_PAGE_SHOTS`` to a folder to save the screenshots quoted in the pull request.
"""

from __future__ import annotations

import os
import time
from datetime import time as clock
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.commons.enums import TicketSource
from src.database.models import PatientNotificationPreference, Queue
from src.modules.queue.sequence import issue_ticket

pytestmark = pytest.mark.postgres

SHOTS = os.environ.get("TICKET_PAGE_SHOTS", "")


def _until(page: Any, predicate: str, timeout: float = 15.0) -> None:
    started = time.monotonic()
    while not page.evaluate(predicate):
        assert time.monotonic() - started < timeout, (
            f"timed out waiting for {predicate}"
        )
        time.sleep(0.05)


def _open_settings(page: Any, path: str) -> None:
    page.goto(path)
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")
    page.locator("#tk-settings summary").click()
    # Loaded: the form shows the server's answer (no channel chosen yet).
    page.wait_for_load_state("networkidle")


def _saved(page: Any) -> str:
    """The note the save leaves: the form clears the last one when it is submitted."""
    _until(page, "() => !document.getElementById('tk-set-note').hidden")
    return str(page.locator("#tk-set-note").inner_text())


def _preference(
    patient_day: SimpleNamespace, patient_id: str
) -> PatientNotificationPreference | None:
    with patient_day.clinic.session() as db:
        return db.get(PatientNotificationPreference, patient_id)


def test_a_shared_link_sets_quiet_hours_and_mutes_a_message(
    patient_day: SimpleNamespace,
) -> None:
    mine = patient_day.ticket(ahead=1)
    page = patient_day.follower_page(height=1600)  # tall enough to show the whole panel
    _open_settings(page, mine.path)

    page.locator("#tk-set-quiet-start").fill("21:00")
    page.locator("#tk-set-quiet-end").fill("07:00")
    page.locator(".tk-mute input[value='transferred']").check()
    page.locator("#tk-set-channel").select_option("sms")
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.locator("#tk-settings").screenshot(
            path=str(Path(SHOTS) / "message-settings.png")
        )
    page.locator("#tk-set-save").click()

    assert _saved(page) == "Saved. Your next message follows these settings."
    stored = _preference(patient_day, mine.patient_id)
    assert stored is not None
    assert (stored.quiet_hours_start, stored.quiet_hours_end) == (
        clock(21, 0),
        clock(7, 0),
    )
    assert stored.muted_events == ["transferred"] and stored.preferred_channel == "sms"
    assert stored.opted_out_at is None


def test_stopping_from_the_page_is_an_opt_out_and_half_a_window_is_refused(
    patient_day: SimpleNamespace,
) -> None:
    mine = patient_day.ticket(ahead=1)
    page = patient_day.owner_page(mine, height=1600)
    _open_settings(page, mine.path)

    page.locator("#tk-set-quiet-start").fill("22:00")
    page.locator("#tk-set-save").click()
    assert _saved(page) == "Quiet hours need both a start and an end, or neither."
    assert _preference(patient_day, mine.patient_id) is None, "nothing was sent"

    page.locator("#tk-set-quiet-start").fill("")
    page.locator("#tk-set-stop").check()
    page.locator("#tk-set-save").click()
    _until(
        page,
        "() => document.getElementById('tk-set-note').textContent.startsWith('Saved')",
    )
    assert _saved(page) == "Saved. You will get no more messages about your tickets."
    stored = _preference(patient_day, mine.patient_id)
    assert stored is not None and stored.opted_out_at is not None
    assert stored.opted_out_via == "ticket_page"
    if SHOTS:
        page.locator("#tk-settings").screenshot(
            path=str(Path(SHOTS) / "message-settings-stopped.png")
        )


def test_a_walk_in_has_no_settings_and_the_panel_fits_320_px(
    patient_day: SimpleNamespace,
) -> None:
    with patient_day.clinic.session() as db:
        walk_in = issue_ticket(
            db,
            queue=db.get_one(Queue, patient_day.clinic.queue),
            source=TicketSource.WALK_IN,
        )
        db.commit()
    page = patient_day.follower_page()
    page.goto(f"/t/{walk_in.page_token}")
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")
    assert page.locator("#tk-settings").count() == 0

    mine = patient_day.ticket(ahead=0)
    narrow = patient_day.follower_page(width=320)
    _open_settings(narrow, mine.path)
    widest = narrow.evaluate(
        "() => Math.max(...[...document.querySelectorAll('#tk-settings *')].map(e => e.getBoundingClientRect().right))"
    )
    offenders = narrow.evaluate(
        "() => [...document.querySelectorAll('#tk-settings *')].filter(e => e.getBoundingClientRect().right > 320).map(e => e.tagName + '#' + e.id + '.' + e.className)"
    )
    assert widest <= 320, (widest, offenders)
    assert narrow.evaluate("() => document.documentElement.scrollWidth") <= 320
