"""Joining a queue from the web, in a real browser (Issue 200).

Chromium against the real server and a migrated database, with the page's own scripts. What is shown:

* **a new patient goes from the clinic page to their ticket page** by phone number, code, the notifications
  answer and a queue, with the keyboard, on a 320 px screen, and the answer given is the one recorded;
* **each refusal is the API's own sentence on the page**: a number that cannot be read, a wrong code with
  the tries left, and a second code inside the cooldown, which holds the send button and counts down;
* **a signed-in patient starts at the queue**, and joining the queue they already hold opens that ticket;
* **the installed app's start page signs a patient in and opens their ticket**, without leaving ``/t/``;
* **the home page's Your ticket** is in the top bar on a 320 px phone, on one line, and opens ``/t/``.

Set ``TICKET_PAGE_SHOTS`` to a folder to save the screenshots quoted in the pull request.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from src.commons.enums import ConsentPurpose
from src.commons.phone import INVALID_PHONE_MESSAGE
from src.core import otp_store
from src.database.models import Patient, Site, Ticket
from src.modules.notifications import service as notification_service
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.patients.consent import has_consent
from src.modules.patients.consent_text import CONSENT_WORDING
from src.web import discover, join
from tests.e2e.patient.conftest import SITE

pytestmark = pytest.mark.postgres

SHOTS = os.environ.get("TICKET_PAGE_SHOTS", "")
_TICKET_PATH = re.compile(r".*/t/[A-Za-z0-9_-]{43}$")


def _until(page: Any, predicate: str, timeout: float = 15.0) -> None:
    started = time.monotonic()
    while not page.evaluate(predicate):
        assert time.monotonic() - started < timeout, (
            f"timed out waiting for {predicate}"
        )
        time.sleep(0.05)


def _shot(page: Any, name: str, *, full_page: bool = True) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        # From the top: a full-page capture of a scrolled page draws the sticky header mid-page.
        page.evaluate("() => window.scrollTo(0, 0)")
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=full_page)


@pytest.fixture
def joining(
    patient_day: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    """Joining from a phone switched on for this server, and every SMS kept by a fake the test reads."""
    clinic = patient_day.clinic
    on = clinic.settings.model_copy(update={"patient_join_enabled": True})
    # The clinic page's button and the join page each read the flag.
    monkeypatch.setattr(discover, "get_settings", lambda: on)
    monkeypatch.setattr(join, "get_settings", lambda: on)
    sms = FakeSmsProvider()
    monkeypatch.setattr(notification_service, "build_sms_provider", lambda: sms)
    otp_store.reset_otp_state()
    with clinic.session() as db:
        slug = db.get_one(Site, SITE).slug

    def code() -> str:
        """The code in the latest SMS."""
        _until_sent = time.monotonic()
        while not sms.sent:
            assert time.monotonic() - _until_sent < 10, "no SMS was sent"
            time.sleep(0.05)
        match = re.search(r"\b(\d{6})\b", sms.sent[-1].text)
        assert match is not None
        return match.group(1)

    yield SimpleNamespace(
        day=patient_day,
        clinic=clinic,
        sms=sms,
        code=code,
        join_path=f"/discover/clinics/{slug}/join",
        slug=slug,
    )


def _error(page: Any, element: str = "sign-in-error") -> str:
    _until(page, f"() => !document.getElementById('{element}').hidden")
    return str(page.locator(f"#{element}").inner_text())


def test_a_new_patient_joins_from_the_clinic_page_by_keyboard_on_a_320_px_screen(
    joining: SimpleNamespace,
) -> None:
    page = joining.day.follower_page(width=320, height=720)
    page.goto(f"/discover/clinics/{joining.slug}")
    page.get_by_role("link", name="Join the queue").click()
    page.wait_for_url(f"**{joining.join_path}")
    _shot(page, "join-1-phone")

    page.locator("#sign-in-phone").fill("082 555 0301")
    page.keyboard.press("Enter")
    _until(page, "() => !document.querySelector('[data-sign-in-step=\"code\"]').hidden")
    assert page.evaluate("() => document.activeElement.id") == "sign-in-code"
    assert page.locator("#sign-in-resend").is_disabled()
    assert "You can ask for a new code in" in page.locator("#sign-in-wait").inner_text()
    _shot(page, "join-2-code")

    page.keyboard.type(joining.code())
    page.keyboard.press("Enter")
    _until(page, "() => !document.querySelector('[data-join-step=\"consent\"]').hidden")
    assert page.evaluate("() => document.activeElement.id") == "join-consent-heading"
    assert (
        page.locator("#join-consent-form legend").inner_text()
        == CONSENT_WORDING[ConsentPurpose.NOTIFICATIONS]
    )
    _shot(page, "join-3-consent")

    page.get_by_label("Yes, message me").check()
    page.get_by_role("button", name="Continue").click()
    _until(page, "() => !document.querySelector('[data-join-step=\"queue\"]').hidden")
    assert (
        page.locator("#join-messages-state").inner_text()
        == "We will message you about your turn."
    )
    # The clinic has one queue that takes remote joins, so it is already chosen.
    assert page.get_by_label(re.compile("Triage")).is_checked()
    page.locator("#join-reason").fill("Headache since Monday")
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 320
    _shot(page, "join-4-queue")

    page.get_by_role("button", name="Join the queue").click()
    page.wait_for_url(_TICKET_PATH)
    assert page.locator("#tk-number").inner_text().startswith("T")
    _shot(page, "join-5-ticket")

    with joining.clinic.session() as db:
        patient = db.execute(
            select(Patient).where(Patient.phone_e164 == "+27825550301")
        ).scalar_one()
        ticket = db.execute(
            select(Ticket).where(Ticket.patient_id == patient.id)
        ).scalar_one()
        assert has_consent(db, patient.id, ConsentPurpose.NOTIFICATIONS) is True
        assert ticket.reason_text == "Headache since Monday"
        assert page.url.endswith(f"/t/{ticket.page_token}")


def test_each_refusal_shows_the_apis_own_sentence(joining: SimpleNamespace) -> None:
    page = joining.day.follower_page()
    page.goto(joining.join_path)

    page.locator("#sign-in-phone").fill("123")
    page.get_by_role("button", name="Send me a code").click()
    assert _error(page) == INVALID_PHONE_MESSAGE
    assert page.locator("#sign-in-error").get_attribute("role") == "alert"

    page.locator("#sign-in-phone").fill("082 555 0302")
    page.get_by_role("button", name="Send me a code").click()
    _until(page, "() => !document.querySelector('[data-sign-in-step=\"code\"]').hidden")
    page.locator("#sign-in-code").fill("000000")
    page.get_by_role("button", name="Sign in").click()
    assert re.fullmatch(r"That code is not right\. \d tries left\.", _error(page))

    # The same number again inside the cooldown: refused, and "Send me a code" waits it out.
    page.get_by_role("button", name="Use a different number").click()
    assert page.locator("#sign-in-send").is_enabled()
    page.get_by_role("button", name="Send me a code").click()
    _until(
        page,
        "() => document.getElementById('sign-in-error').textContent.startsWith('Too many')",
    )
    assert _error(page) == "Too many code requests. Try again shortly."
    assert page.locator("#sign-in-send").is_disabled()
    assert "You can ask for a new code in" in page.locator("#sign-in-wait").inner_text()
    _shot(page, "join-refused-cooldown")
    assert len(joining.sms.sent) == 1


def test_a_signed_in_patient_starts_at_the_queue_and_opens_the_ticket_they_hold(
    joining: SimpleNamespace,
) -> None:
    mine = joining.day.ticket(ahead=1)
    page = joining.day.owner_page(mine)
    page.goto(joining.join_path)
    assert page.locator('[data-sign-in-step="phone"]').is_hidden()
    assert page.locator('[data-join-step="queue"]').is_visible()

    page.get_by_role("button", name="Join the queue").click()
    page.wait_for_url(_TICKET_PATH)
    assert page.url.endswith(mine.path)
    with joining.clinic.session() as db:
        assert (
            len(
                db.scalars(
                    select(Ticket).where(Ticket.patient_id == mine.patient_id)
                ).all()
            )
            == 1
        )


def test_the_app_start_page_signs_a_patient_in_and_opens_their_ticket(
    joining: SimpleNamespace,
) -> None:
    mine = joining.day.ticket(ahead=0)
    with joining.clinic.session() as db:
        phone = db.get_one(Patient, mine.patient_id).phone_e164
    page = joining.day.follower_page(width=320, height=720)
    page.goto("/t/")
    assert page.get_by_role("heading", name="No open ticket on this phone").is_visible()
    _shot(page, "join-app-start")

    page.locator("#sign-in-phone").fill(phone)
    page.get_by_role("button", name="Send me a code").click()
    _until(page, "() => !document.querySelector('[data-sign-in-step=\"code\"]').hidden")
    page.locator("#sign-in-code").fill(joining.code())
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(_TICKET_PATH)
    assert page.url.endswith(mine.path)


def test_the_home_pages_your_ticket_fits_a_320_px_phone_and_opens_the_app_start_page(
    joining: SimpleNamespace,
) -> None:
    """The top bar's links are hidden on a phone, so Your ticket sits with Sign in, and neither wraps."""
    page = joining.day.follower_page(width=320, height=640)
    page.goto("/")
    link = page.get_by_role("banner").get_by_role("link", name="Your ticket")
    assert link.is_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 320
    heights = page.evaluate(
        "() => [...document.querySelectorAll('.lp-nav-actions .lp-btn')].map((b) => b.getBoundingClientRect().height)"
    )
    assert len(set(heights)) == 1 and heights[0] < 44, heights
    _shot(page, "home-your-ticket", full_page=False)

    link.click()
    page.wait_for_url("**/t/")
    assert page.get_by_role("heading", name="No open ticket on this phone").is_visible()
