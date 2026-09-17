"""The clinic dashboard in a real browser: the interactions and the bad-connection paths (Issue 55).

What the HTTP suites cannot show, driven through Chromium against a real server and PostgreSQL:

* **Call next** answers at once and a double click calls one patient (Issue 50);
* **a walk-in** by keyboard alone takes the next number, well inside ten seconds (Issue 51);
* **moving a patient forward** needs a reason and leaves a staff-only badge (Issue 52);
* **the offline path:** losing the network shows *Offline* within ten seconds, a dead stream reconnects
  with backoff and falls back to polling, and coming back restores live updates without a reload;
* **an action pressed offline** is sent on reconnect exactly once, or, when it waited too long, is
  reported as not sent and changes nothing, never silently dropped;
* **an expired session** asks the person to sign in again in place and then finishes the action.

Each test says in its name what a receptionist would see.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.sync_api import expect
from sqlalchemy import select

from src.commons.enums import ActorKind, TicketStatus
from src.database.models import Queue, Ticket
from src.modules.queue.lifecycle import Actor, call_next
from tests.e2e.dashboard.conftest import EMAIL_DOMAIN, OUTBOX_EXPIRY_SECONDS
from tests.factories import FACTORY_STAFF_PASSWORD

pytestmark = pytest.mark.postgres


def _board(clinic: SimpleNamespace) -> str:
    return f"/dashboard/sites/{clinic.site}/board"


def _statuses(clinic: SimpleNamespace, queue_id: str) -> dict[str, str]:
    with clinic.session() as db:
        rows = db.execute(
            select(Ticket.number, Ticket.status).where(Ticket.queue_id == queue_id)
        ).all()
    return dict(rows)


def _card(page: Any, name: str) -> Any:
    return page.locator(
        "[data-queue-id]", has=page.locator(".queue-card-name", has_text=name)
    )


def _live(page: Any, timeout: float = 15_000) -> None:
    expect(page.locator("#board-connection")).to_have_attribute(
        "data-state", "live", timeout=timeout
    )


def test_call_next_answers_at_once_and_a_double_click_calls_one_patient(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 3)
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    triage = _card(page, "Triage")

    triage.locator("button.queue-call-next").dblclick()

    expect(triage.locator("[data-action-msg]")).to_have_text("Called T001.")
    expect(triage.locator('.with-staff-ticket[data-number="T001"]')).to_be_visible()
    assert _statuses(fresh_day, fresh_day.triage) == {
        "T001": TicketStatus.CALLED.value,
        "T002": TicketStatus.WAITING.value,
        "T003": TicketStatus.WAITING.value,
    }


def test_a_walk_in_by_keyboard_alone_takes_the_next_number_well_inside_ten_seconds(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 2)
    page = signed_in("desk", f"/dashboard/sites/{fresh_day.site}/walk-in")
    expect(page.locator("#walkin-name")).to_be_focused()

    started = time.monotonic()
    page.keyboard.type("Gogo M", delay=80)
    page.keyboard.press("Enter")
    expect(page.locator("[data-result-number]")).to_have_text("T003")
    elapsed = time.monotonic() - started

    assert elapsed < 10, elapsed
    expect(page.locator("#walkin-name")).to_be_focused()
    expect(page.locator(".walkin-recent-item").first).to_have_attribute(
        "data-number", "T003"
    )


def test_moving_a_patient_forward_needs_a_reason_and_leaves_a_priority_badge(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 3)
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    triage = _card(page, "Triage")
    triage.locator("summary.queue-line-toggle").click()
    triage.locator('.line-ticket[data-number="T003"] [data-move-ticket]').click()
    dialog = page.locator("#move-dialog")
    expect(dialog).to_be_visible()

    page.locator("#move-save").click()
    expect(page.locator("#move-msg")).not_to_be_empty()  # no reason: not saved
    page.select_option("#move-ahead", index=0)  # call T003 before the first in line
    dialog.locator('input[name="reason"]').first.check()
    page.locator("#move-save").click()

    expect(dialog).to_be_hidden()
    first = _card(page, "Triage").locator(".line-ticket").first
    expect(first).to_have_attribute("data-number", "T003", timeout=10_000)
    expect(first.locator(".line-priority")).to_have_text("Priority")


def test_losing_the_network_shows_offline_within_ten_seconds_and_comes_back_live_without_a_reload(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 2)
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    loaded = page.evaluate("performance.timeOrigin")
    status = page.locator("#board-connection")

    page.context.set_offline(True)
    expect(status).to_have_attribute("data-state", "offline", timeout=10_000)
    expect(status).to_contain_text("Offline, data from")

    page.context.set_offline(False)
    _live(page, timeout=20_000)
    # Live again means live: a change made elsewhere now arrives without anyone reloading.
    with fresh_day.session() as db:
        call_next(
            db,
            db.get(Queue, fresh_day.triage),
            actor=Actor(kind=ActorKind.STAFF, label="nurse"),
        )
        db.commit()
    expect(_card(page, "Triage").locator('[data-count="waiting"]')).to_have_text(
        "1", timeout=5_000
    )
    assert page.evaluate("performance.timeOrigin") == loaded


def test_a_dead_stream_is_retried_with_a_growing_wait_then_polled_and_goes_live_when_it_returns(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 1)
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    status = page.locator("#board-connection")

    page.route("**/board/stream", lambda route: route.abort())
    page.evaluate(
        "window.dispatchEvent(new Event('online'))"
    )  # reconnect now, into the blocked stream
    expect(status).to_contain_text("Reconnecting (attempt 1)", timeout=10_000)
    expect(status).to_have_attribute("data-state", "polling", timeout=20_000)
    assert int(status.get_attribute("data-attempt") or 0) >= 3

    page.unroute("**/board/stream")
    _live(page, timeout=40_000)


def test_call_next_pressed_while_offline_is_sent_when_the_network_returns_exactly_once(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 2)
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    triage = _card(page, "Triage")
    outbox = page.locator("#action-outbox")

    page.context.set_offline(True)
    expect(page.locator("#board-connection")).to_have_attribute(
        "data-state", "offline", timeout=10_000
    )
    triage.locator("button.queue-call-next").click()
    triage.locator(
        "button.queue-call-next"
    ).click()  # pressed again while it waits: still one

    expect(outbox).to_contain_text("Waiting for the connection: Call next in Triage")
    expect(outbox.locator(".outbox-item")).to_have_count(1)
    assert _statuses(fresh_day, fresh_day.triage) == {
        "T001": "waiting",
        "T002": "waiting",
    }

    page.context.set_offline(False)
    expect(outbox).to_contain_text(
        "Done: Call next in Triage (T001), sent when the connection came back.",
        timeout=20_000,
    )
    assert _statuses(fresh_day, fresh_day.triage) == {
        "T001": "called",
        "T002": "waiting",
    }
    expect(_card(page, "Triage").locator("[data-action-msg]")).to_contain_text("T001")


def test_an_action_that_waited_too_long_is_reported_not_sent_and_changes_nothing(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    walk_ins(fresh_day.triage, 1)
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    outbox = page.locator("#action-outbox")

    page.context.set_offline(True)
    expect(page.locator("#board-connection")).to_have_attribute(
        "data-state", "offline", timeout=10_000
    )
    _card(page, "Triage").locator("button.queue-call-next").click()
    expect(outbox).to_contain_text("Waiting for the connection")
    page.wait_for_timeout((OUTBOX_EXPIRY_SECONDS + 1) * 1000)
    page.context.set_offline(False)

    expect(outbox).to_contain_text("Not sent:", timeout=20_000)
    expect(outbox).to_contain_text(
        "waited too long for the connection, so it was not sent and nothing was changed"
    )
    assert _statuses(fresh_day, fresh_day.triage) == {"T001": "waiting"}


def test_an_expired_session_asks_to_sign_in_again_and_then_finishes_the_action(
    fresh_day: SimpleNamespace,
    walk_ins: Callable[[str, int], list[str]],
    signed_in: Callable[..., Any],
) -> None:
    """Issue 231: signing in again is a trip to ``/signin``, not a dialog over the board.

    What has to survive the trip is the held action. The outbox keeps its queue in this tab's
    session storage and marks it ready to send again on the way back, so the round trip finishes
    the work the same way the modal did — and the "unsent work" prompt must not fire on the way
    there, or the redirect would stop at a browser dialog.
    """
    walk_ins(fresh_day.triage, 1)
    with fresh_day.session() as db:
        call_next(
            db,
            db.get(Queue, fresh_day.triage),
            actor=Actor(kind=ActorKind.STAFF, label="desk"),
        )
        db.commit()
    page = signed_in("desk", _board(fresh_day))
    _live(page)
    # The session ends: the access and refresh cookies are gone; the page and its CSRF token remain.
    context = page.context
    kept = [
        cookie
        for cookie in context.cookies()
        if cookie["name"]
        not in {
            fresh_day.settings.access_token_cookie_name,
            fresh_day.settings.refresh_token_cookie_name,
        }
    ]
    context.clear_cookies()
    context.add_cookies(kept)

    _card(page, "Triage").locator(
        '.with-staff-ticket[data-number="T001"] button', has_text="Start"
    ).click()

    # The board sends the caller to the sign-in page, saying why and carrying the way back.
    page.wait_for_url("**/signin?**", timeout=10_000)
    assert "expired=1" in page.url
    expect(page.locator(".lp-auth-notice")).to_contain_text("Your session ended")

    page.fill("#signin-email", f"desk@{EMAIL_DOMAIN}")
    page.fill("#signin-password", FACTORY_STAFF_PASSWORD)
    page.click("#btn-password-login")

    # Back on the board it came from, with the held action sent.
    page.wait_for_url(lambda url: "/signin" not in url, timeout=15_000)
    expect(page.locator("#action-outbox")).to_contain_text(
        "Done: Start for T001", timeout=15_000
    )
    assert _statuses(fresh_day, fresh_day.triage) == {
        "T001": TicketStatus.IN_PROGRESS.value
    }
    _live(page, timeout=20_000)
