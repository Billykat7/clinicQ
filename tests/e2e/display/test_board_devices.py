"""Kiosk screens in Chromium: paired in under two minutes with no address typed, removed, and back after a power cut (Issue 61).

A "box" here is a fresh browser context, nothing signed in, that opens only ``/display``, the one address
every box is provisioned with (``docs/OPS/KIOSK_SETUP.md``). The clinic manager is a second browser,
signed in, using the dashboard's *Display boards* tab the way a person would: typing the code from the
box's screen and pressing *Pair*.

* **Paired in under two minutes, no address typed at the clinic.** Timed from the moment the box shows its
  code to the moment it shows the board by itself.
* **Removed means gone.** The manager removes the screen, and the box leaves the board for a new pairing
  code by itself, without a reload.
* **A board address is not a key.** The board's address, opened in a browser that is not a paired screen,
  shows the pairing screen, not the board.
* **A power cut.** The box's browser is closed and a new one started with what the old one had stored on
  disk (its cookies). Opening ``/display`` goes straight to the board, with nobody touching it.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

#: The criterion: a new box pairs in under two minutes.
PAIRING_BUDGET_SECONDS = 120


def _until(page: Any, predicate: str, timeout: float = 30.0) -> float:
    """Wait until ``predicate`` is true in the page (asked with ``evaluate``); seconds it took."""
    started = time.monotonic()
    while True:
        try:
            if page.evaluate(predicate):
                return time.monotonic() - started
        except Exception:  # the page is navigating: ask again
            pass
        if time.monotonic() - started > timeout:
            raise AssertionError(f"timed out waiting for {predicate}")
        time.sleep(0.05)


def _pair_in_dashboard(day: SimpleNamespace, code: str, label: str) -> Any:
    """The manager types the code and a name into *Display boards* and presses Pair."""
    manager = day.manager_page()
    manager.goto(f"/dashboard/sites/{day.clinic.site}/settings/devices")
    manager.fill("#pair-code", code)
    manager.fill("#pair-label", label)
    manager.click(".pair-form button[type=submit]")
    return manager


def test_a_fresh_box_pairs_in_under_two_minutes_with_no_address_typed(
    board_day: SimpleNamespace,
) -> None:
    """The box opens /display, shows a code; the manager types it; the box shows the board by itself."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 3)
    box = board_day.new_page(1280, 720, paired=False)
    # The provisioned start address: nobody at the clinic types it.
    box.goto("/display")
    box.wait_for_selector(".pair-code")
    shown_at = time.monotonic()
    code = box.locator(".pair-code").text_content().strip()
    assert len(code.replace(" ", "")) == 6

    manager = _pair_in_dashboard(board_day, code, "TV by reception")
    _until(
        box,
        "() => location.pathname.startsWith('/display/') && !!document.querySelector('.panel')",
    )
    took = time.monotonic() - shown_at
    print(f"\npaired and showing the board {took:.1f} s after the code appeared")  # noqa: T201 — quoted in the PR
    assert took < PAIRING_BUDGET_SECONDS
    assert box.url.endswith(f"/display/{board_day.clinic.site}")
    manager.goto(f"/dashboard/sites/{board_day.clinic.site}/settings/devices")
    assert manager.locator("#dv-table tbody tr").count() == 1
    assert "TV by reception" in manager.locator("#dv-table tbody").text_content()


def test_a_removed_screen_leaves_the_board_by_itself_for_a_new_pairing_code(
    board_day: SimpleNamespace,
) -> None:
    """The manager removes it in the dashboard; within seconds the box shows a pairing code, not the board."""
    board_day.open_queues(1)
    box = board_day.new_page(1280, 720, paired=False)
    box.goto("/display")
    box.wait_for_selector(".pair-code")
    manager = _pair_in_dashboard(
        board_day, box.locator(".pair-code").text_content(), "TV to remove"
    )
    _until(box, "() => !!document.querySelector('.panel')")

    manager.goto(f"/dashboard/sites/{board_day.clinic.site}/settings/devices")
    manager.locator("#dv-table tbody tr").first.click()
    manager.locator("#dv-detail button.btn-danger").click()
    manager.locator("#confirm-yes").click()
    removed_at = time.monotonic()
    _until(box, "() => !!document.querySelector('.pair-code')", timeout=60)
    took = time.monotonic() - removed_at
    print(f"\nremoved screen back on its pairing code {took:.1f} s after Remove")  # noqa: T201 — quoted in the PR
    assert box.url.endswith("/display")


def test_a_board_address_opened_in_a_browser_that_is_not_a_paired_screen_shows_no_board(
    board_day: SimpleNamespace,
) -> None:
    """Copying the board's address from a screen to a phone gets a pairing code, never the numbers."""
    board_day.open_queues(1)
    stranger = board_day.new_page(1280, 720, paired=False)
    stranger.goto(f"/display/{board_day.clinic.site}")
    stranger.wait_for_selector(".pair-code")
    assert stranger.url.endswith("/display")
    assert stranger.locator(".panel").count() == 0


def test_after_a_power_cut_the_box_returns_to_its_board_with_nobody_touching_it(
    board_day: SimpleNamespace, tmp_path: Any
) -> None:
    """Close the box's browser, start a new one from what it had stored, open /display: the board."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 2)
    before = board_day.new_page(1280, 720)
    before.goto("/display")
    _until(before, "() => !!document.querySelector('.panel')")
    stored = tmp_path / "box-profile.json"
    before.context.storage_state(path=str(stored))
    before.context.close()  # the power goes

    after = board_day.clinic.browser.new_context(
        base_url=board_day.clinic.base_url,
        viewport={"width": 1280, "height": 720},
        storage_state=str(stored),
    )
    board_day.track(after)
    booted = after.new_page()
    booted.goto("/display")  # what the box's autostart opens after the reboot
    _until(booted, "() => !!document.querySelector('.panel')")
    assert booted.url.endswith(f"/display/{board_day.clinic.site}")
