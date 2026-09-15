"""The patient's ticket page in a real browser (Issue 68).

The behaviour a test of JSON cannot show, measured on the page rather than read from its HTML:

* **position and estimate update live without a manual refresh**: the desk calls the patients ahead and
  the open page moves from 3rd to "you are next" to "please come in now", with no navigation;
* **"you are next" is impossible to miss**: the alert covers the top of the screen, the tab title says
  it, and it is not the same colour as anything else on the page;
* **the page shows how stale its data is whenever updates stop**: the router dies, and within the
  page's stale threshold it says "Not live" and how old its numbers are, keeping them;
* **cancelling takes two taps and confirms clearly**, and a phone that was only sent the link is not
  offered it;
* **the page works on a 320 px screen**: nothing is wider than the screen.

Set ``TICKET_PAGE_SHOTS`` to a folder to save the screenshots quoted in the pull request.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.e2e.conftest import router

pytestmark = pytest.mark.postgres

SHOTS = os.environ.get("TICKET_PAGE_SHOTS", "")

_STATE = """() => ({
    headline: document.getElementById('tk').dataset.headline,
    position: document.querySelector('[data-fill="position"]').textContent,
    wait: document.querySelector('[data-fill="wait"]').textContent,
    title: document.title,
    alert: !document.getElementById('tk-alert').hidden,
    live: document.getElementById('tk-live').textContent,
    stale: !document.getElementById('tk-stale').hidden,
    staleText: document.getElementById('tk-stale').textContent.replace(/\\s+/g, ' ').trim(),
    navigations: performance.getEntriesByType('navigation').length,
})"""


def _until(page: Any, predicate: str, timeout: float = 20.0) -> float:
    """Wait until ``predicate`` is true in the page; seconds it took."""
    started = time.monotonic()
    while True:
        try:
            if page.evaluate(predicate):
                return time.monotonic() - started
        except Exception:  # the page is loading: ask again
            pass
        if time.monotonic() - started > timeout:
            raise AssertionError(f"timed out waiting for {predicate}")
        time.sleep(0.05)


def _shot(page: Any, name: str) -> None:
    """Save a screenshot for the pull request when ``TICKET_PAGE_SHOTS`` names a folder."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def _open(page: Any, path: str) -> None:
    """Open the page and wait for its stream to deliver the first state."""
    page.goto(path)
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")


def test_the_open_page_follows_the_calls_ahead_to_you_are_next_and_come_in_now(
    patient_day: SimpleNamespace,
) -> None:
    """How to verify, step 1: the desk calls the patients ahead; the page moves without a refresh."""
    mine = patient_day.ticket(ahead=2)
    page = patient_day.follower_page()
    _open(page, mine.path)
    before = page.evaluate(_STATE)
    assert (before["headline"], before["position"], before["alert"]) == (
        "waiting",
        "3",
        False,
    )
    assert "–" in before["wait"] and "min" in before["wait"]
    _shot(page, "ticket-waiting-3rd")

    patient_day.call()
    took = _until(
        page,
        "() => document.querySelector('[data-fill=\"position\"]').textContent === '2'",
    )
    patient_day.call()
    took_next = _until(
        page, "() => document.getElementById('tk').dataset.headline === 'next'"
    )
    next_up = page.evaluate(_STATE)
    assert next_up["alert"] and next_up["title"].startswith("You are next")
    colours = page.evaluate(
        """() => {
            const alert = getComputedStyle(document.getElementById('tk-alert')).backgroundColor;
            const others = [...document.querySelectorAll('.tk *:not(#tk-alert):not(#tk-alert *)')]
                .map(el => getComputedStyle(el).backgroundColor);
            const box = document.getElementById('tk-alert').getBoundingClientRect();
            return {alert, reused: others.includes(alert), top: box.top, height: box.height};
        }"""
    )
    assert not colours["reused"], colours
    assert colours["top"] < 120 and colours["height"] > 100, colours
    _shot(page, "ticket-you-are-next")

    patient_day.call()
    _until(page, "() => document.getElementById('tk').dataset.headline === 'called'")
    called = page.evaluate(_STATE)
    assert called["title"].startswith("Come in now") and called["navigations"] == 1
    _shot(page, "ticket-come-in-now")
    message = (
        f"position 3→2 in {took:.2f} s; 2→next in {took_next:.2f} s; one navigation"
    )
    print("\n" + message)  # noqa: T201


def test_when_updates_stop_the_page_says_so_and_how_old_its_numbers_are(
    patient_day: SimpleNamespace,
) -> None:
    """Criterion: the page shows how stale its data is whenever updates stop."""
    mine = patient_day.ticket(ahead=4)
    with router(patient_day.clinic.base_url) as link:
        page = patient_day.follower_page(base_url=link.base_url)
        page.clock.install()
        _open(page, mine.path)
        assert page.evaluate(_STATE)["stale"] is False

        link.cut()
        page.clock.run_for(50_000)  # past the 45 s threshold, on the page's own clock
        state = page.evaluate(_STATE)
        assert state["stale"] and state["live"] == "Not live"
        assert state["position"] == "5", "the last known numbers stay on the screen"
        assert "ago" in state["staleText"] and "s" in state["staleText"]
        _shot(page, "ticket-not-live")
        print(f"\nrouter dead: {state['live']!r}, {state['staleText']!r}")  # noqa: T201

        link.restore()
        # A poll every refresh_seconds brings it back. The page's clock is Playwright's, so keep moving
        # it on: a poll sent on a connection the router reset fails, and the next one is due 15 s later.
        deadline = time.monotonic() + 60
        while not page.evaluate("() => document.getElementById('tk-stale').hidden"):
            assert time.monotonic() < deadline, (
                "the page did not come back after the router did"
            )
            page.clock.run_for(15_000)
            time.sleep(0.5)


def test_cancelling_takes_two_taps_and_says_so_and_a_shared_link_cannot(
    patient_day: SimpleNamespace,
) -> None:
    """How to verify, step 2: two taps and a clear confirmation; the family's phone has no button."""
    mine = patient_day.ticket(ahead=1)
    family = patient_day.follower_page()
    _open(family, mine.path)
    assert family.locator("#tk-cancel-start").is_hidden()

    page = patient_day.owner_page(mine)
    _open(page, mine.path)
    page.locator("#tk-cancel-start").click()  # tap 1
    assert page.locator("#tk-confirm").is_visible()
    _shot(page, "ticket-cancel-confirm")
    page.locator("#tk-cancel-yes").click()  # tap 2
    _until(page, "() => document.getElementById('tk').dataset.headline === 'cancelled'")
    note = page.locator("#tk-cancel-note").inner_text()
    assert note.startswith(f"Ticket {mine.number} is cancelled."), note
    _until(
        family, "() => document.getElementById('tk').dataset.headline === 'cancelled'"
    )
    _shot(page, "ticket-cancelled")


@pytest.mark.parametrize("width", [320, 360])
def test_nothing_is_wider_than_a_320_px_screen(
    patient_day: SimpleNamespace, width: int
) -> None:
    """Criterion: the page works on a 320 px screen, in every headline."""
    mine = patient_day.ticket(ahead=1)
    page = patient_day.owner_page(mine, width=width)
    _open(page, mine.path)
    overflow = "() => document.documentElement.scrollWidth <= window.innerWidth"
    assert page.evaluate(overflow)
    patient_day.call()
    _until(page, "() => document.getElementById('tk').dataset.headline === 'next'")
    assert page.evaluate(overflow)
    patient_day.call()
    _until(page, "() => document.getElementById('tk').dataset.headline === 'called'")
    assert page.evaluate(overflow)
    if width == 320:
        _shot(page, "ticket-320px-called")
