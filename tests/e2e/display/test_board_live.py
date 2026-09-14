"""The waiting-room board kept live, in Chromium against a real server on PostgreSQL (Issue 57).

The criteria, each demonstrated rather than assumed:

* **Within 2 seconds.** Call next, then time until the number is on the board, five times.
* **A dead connection heals with a full resync.** The server is stopped under an open board and started
  again on the same port. A call made during the outage is on the board afterwards, and the page never
  reloaded.
* **A missed heartbeat is noticed within 30 seconds and shown.** A server that stops beating (the stream
  stays open, nothing arrives) is reported as "Reconnecting to the clinic…" between 30 and 31 seconds of
  silence on the page's clock, and the board reconnects on its own.
* **A 10-minute outage recovers with no one touching it.**
* **Connections do not pile up.** Over a simulated day, boards connect and vanish without closing their
  connection; the server holds only the streams that are still open, and none at the end.

The test server beats every half second (``SERVER_HEARTBEAT_SECONDS``); the page still expects a beat every
15 seconds, and its clock is Playwright's where the test needs minutes to pass.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.core import live_events
from src.web import display_stream
from tests.e2e.conftest import serve

pytestmark = pytest.mark.postgres

#: The criterion: a call reaches the board within this many seconds.
CALL_BUDGET_SECONDS = 2.0
#: The board's connection state, as a page expression.
_STATE_OF = "(window.ClinicQBoardLive && window.ClinicQBoardLive.state().state)"
_STATE = f"() => {_STATE_OF}"
_SHOWS_NUMBER = "(number) => [...document.querySelectorAll('.serving-number')].some((el) => el.textContent === number)"


def _until(
    page: Any, predicate: str, argument: Any = None, timeout: float = 30.0
) -> float:
    """Wait until ``predicate`` is true in the page; how long it took, in seconds."""
    started = time.monotonic()
    while not page.evaluate(predicate, argument):
        if time.monotonic() - started > timeout:
            raise AssertionError(f"timed out waiting for {predicate} ({argument!r})")
        time.sleep(0.02)
    return time.monotonic() - started


def _live_board(
    day: SimpleNamespace, base_url: str | None = None, *, clock: bool = False
) -> Any:
    """A board page whose stream is open (``live``)."""
    options = {"base_url": base_url} if base_url else {}
    page = day.new_page(1280, 720, **options)
    if clock:
        page.clock.install()
    page.goto(day.clinic.page_path)
    page.wait_for_selector(".panel")
    _until(page, f"() => {_STATE_OF} === 'live'")
    return page


def test_a_call_reaches_the_board_within_two_seconds(
    board_day: SimpleNamespace,
) -> None:
    """Five calls, each timed from the commit to the number on the screen."""
    triage, *_ = board_day.open_queues(2)
    board_day.issue(triage, 6)
    page = _live_board(board_day)
    timings = []
    for _ in range(5):
        started = time.monotonic()
        number = board_day.call(triage)
        _until(page, _SHOWS_NUMBER, number, timeout=CALL_BUDGET_SECONDS * 5)
        timings.append(time.monotonic() - started)
    print(f"\ncall to screen: {[f'{t * 1000:.0f} ms' for t in timings]}")  # noqa: T201 — quoted in the PR
    assert max(timings) < CALL_BUDGET_SECONDS, timings


def test_a_restarted_server_is_rejoined_with_a_full_resync_and_no_reload(
    board_day: SimpleNamespace,
) -> None:
    """Stop the server under an open board, call a patient meanwhile, start it again: the board catches up."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 4)
    app = board_day.clinic.app
    with serve(app) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        page = _live_board(board_day, base_url)
        page.evaluate("() => { window.__notReloaded = true; }")
        first = board_day.call(triage)
        _until(page, _SHOWS_NUMBER, first)

    # The server is gone: the page notices the stream closed and says so.
    waited = _until(page, f"() => {_STATE_OF} === 'reconnecting'")
    assert page.locator("[data-board-connection]").is_visible()
    assert (
        page.locator("[data-board-connection]").text_content()
        == "Reconnecting to the clinic…"
    )
    during_outage = board_day.call(triage)

    with serve(app, port=port):
        recovered = _until(page, f"() => {_STATE_OF} === 'live'", timeout=40)
        _until(page, _SHOWS_NUMBER, during_outage)
        assert page.evaluate("() => window.__notReloaded === true")
        assert page.locator("[data-board-connection]").is_hidden()
    report = f"noticed the restart in {waited:.2f} s, live again {recovered:.2f} s after it returned"
    print(f"\n{report}")  # noqa: T201 — quoted in the PR


def test_a_silent_connection_is_noticed_within_thirty_seconds_shown_and_healed(
    board_day: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stream stays open but nothing arrives: "Reconnecting" after two missed beats, then live again."""
    board_day.open_queues(1)
    # This test's server never beats: the connection is open, and silent.
    monkeypatch.setattr(display_stream, "BOARD_HEARTBEAT_SECONDS", 3600)
    page = _live_board(board_day, clock=True)

    page.clock.run_for(29_000)
    assert page.evaluate(_STATE) == "live"
    assert page.locator("[data-board-connection]").is_hidden()

    page.clock.run_for(2_000)
    assert page.evaluate(_STATE) == "reconnecting"
    assert (
        page.locator("[data-board-connection]").text_content()
        == "Reconnecting to the clinic…"
    )
    assert page.locator("[data-board-connection]").is_visible()

    # The retry waits at most a second on the first attempt, and the new stream's full board goes live.
    page.clock.run_for(1_000)
    _until(page, f"() => {_STATE_OF} === 'live'")


def test_a_ten_minute_outage_recovers_with_nobody_touching_the_board(
    board_day: SimpleNamespace,
) -> None:
    """Ten minutes without a server on the page's clock, then the server returns: live, and current."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 4)
    app = board_day.clinic.app
    with serve(app) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        page = _live_board(board_day, base_url, clock=True)

    _until(page, f"() => {_STATE_OF} === 'reconnecting'")
    # Ten minutes pass: every retry fails (connection refused), backing off to 30 seconds apart, and
    # the board polls /state every 10 seconds as well, which fails too.
    for _ in range(20):
        page.clock.run_for(30_000)
        time.sleep(0.05)  # let the refused connections report back
    attempts = page.evaluate("() => window.ClinicQBoardLive.state().attempts")
    assert page.evaluate(_STATE) == "reconnecting"
    called = board_day.call(triage)

    with serve(app, port=port):
        # At most one 30-second backoff, in steps so the new stream can answer.
        for _ in range(8):
            page.clock.run_for(5_000)
            if page.evaluate(_STATE) == "live":
                break
            time.sleep(0.2)
        assert page.evaluate(_STATE) == "live"
        _until(page, _SHOWS_NUMBER, called)
    print(f"\n{attempts} failed attempts in ten minutes, then live and current")  # noqa: T201
    assert attempts >= 10


def test_connections_stay_flat_over_a_simulated_day_of_boards_that_vanish_without_closing(
    board_day: SimpleNamespace,
) -> None:
    """48 half-hours: six boards connect, four vanish mid-stream; the server keeps only the two left.

    Real HTTP streams against the running server. A board that vanishes closes its socket without
    reading on, as a box losing power does; the server notices at its next beat (half a second here).
    """
    board_day.open_queues(1)
    site = board_day.clinic.site
    url = f"{board_day.clinic.base_url}{board_day.clinic.page_path}/stream"
    kept: list[tuple[httpx.Client, httpx.Response, Iterator[str]]] = []
    counts = []

    def open_stream() -> tuple[httpx.Client, httpx.Response, Iterator[str]]:
        client = httpx.Client(
            timeout=httpx.Timeout(10, read=None),
            cookies={board_day.clinic.settings.display_device_cookie_name: secret},
        )
        response = client.send(client.build_request("GET", url), stream=True)
        assert response.status_code == 200
        lines = response.iter_lines()
        next(lines)  # the first line of the full board: the stream is open
        # The iterator is kept with the response: dropping it would close the stream from this side.
        return client, response, lines

    def settle(expected: int) -> int:
        deadline = time.monotonic() + 5
        while live_events.broker.subscriber_count(site) != expected:
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
        return live_events.broker.subscriber_count(site)

    secret = (
        board_day.paired_secret()
    )  # every stream is this clinic's screen, as a box's would be
    baseline = live_events.broker.subscriber_count(site)
    try:
        for _ in range(48):
            opened = [open_stream() for _ in range(6)]
            for client, response, _ in opened[:4]:  # gone without a goodbye
                response.close()
                client.close()
            kept.extend(opened[4:])
            counts.append(settle(baseline + len(kept)) - baseline)
            # Every half hour, the two oldest boards reboot too.
            while len(kept) > 2:
                client, response, _ = kept.pop(0)
                response.close()
                client.close()
    finally:
        for client, response, _ in kept:
            response.close()
            client.close()
    final = settle(baseline) - baseline
    print(f"\nopen streams after each half hour: {counts}; at the end: {final}")  # noqa: T201
    assert all(count <= 4 for count in counts), counts
    assert final == 0
