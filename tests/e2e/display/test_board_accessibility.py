"""The waiting-room board's accessibility, measured in Chromium (Issue 59).

* **Contrast on every board state, as drawn.** Every visible piece of text in every theme is measured
  against the background it actually sits on, in two page states that together hold every board state:
  a called ticket, a new call, a ticket called again, one being seen, a queue with nobody served or waiting,
  the page count, the health notice, the "Reconnecting" line and the "Not up to date" banner (Issue 62).
  Everything must reach 4.5:1.
* **Readable without colour.** Under Chromium's achromatopsia emulation (no colour at all) each status
  still has its own words and its own shape, and the new-call highlight still stands apart from its panel.
  With ``BOARD_A11Y_SHOTS`` set to a folder, the test saves greyscale screenshots of every theme there, the
  evidence attached to the pull request.
* **Reduced motion.** The pulse is gone and an inner ring holds its emphasis still.
* **A theme is a clinic's choice**, and a board follows a change to it at once, with no reload.
"""

from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from src.commons.enums import ActorKind, BoardTheme, LiveEventType, TicketStatus
from src.commons.time import now_sast
from src.core.live_events import LiveEvent, publish_live
from src.database.models import Site, Ticket
from src.modules.queue.lifecycle import Actor, transition_ticket
from tests.e2e.conftest import serve

pytestmark = pytest.mark.postgres

_DESK = Actor(kind=ActorKind.STAFF, label="desk@clinicq.example")
TEXT_MINIMUM = 4.5

#: Every visible text in the board, its colour against the background it is really drawn on.
_CONTRAST = """() => {
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const [r, g, b, a = 1] = m[1].split(',').map((x) => parseFloat(x));
    return { r, g, b, a };
  };
  const luminance = ({ r, g, b }) => {
    const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  };
  const ratio = (x, y) => {
    const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
    return (hi + 0.05) / (lo + 0.05);
  };
  const backgroundOf = (el) => {
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const colour = parse(getComputedStyle(node).backgroundColor);
      if (colour && colour.a > 0) return colour;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const measured = [];
  for (const el of document.querySelectorAll('.kiosk *')) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || el.closest('template') || el.classList.contains('sr-only')) continue;
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height || getComputedStyle(el).visibility === 'hidden') continue;
    const colour = parse(getComputedStyle(el).color);
    measured.push({
      text: el.textContent.trim().slice(0, 40),
      class: el.className,
      ratio: Math.round(ratio(colour, backgroundOf(el)) * 100) / 100,
    });
  }
  return measured;
}"""

_STATUSES = """() => [...document.querySelectorAll('.serving')].map((el) => ({
  status: el.classList.contains('is-new') ? 'new' : el.dataset.status,
  words: el.querySelector('.serving-state').textContent,
  shape: getComputedStyle(el.querySelector('.serving-state'), '::before').content,
  shadow: getComputedStyle(el).boxShadow,
  animation: getComputedStyle(el).animationName,
}))"""


def _set_theme(day: SimpleNamespace, theme: BoardTheme) -> None:
    """Choose the clinic's board theme and tell its open boards, as a manager's save does."""
    with day.clinic.session() as db:
        db.execute(
            update(Site)
            .where(Site.id == day.clinic.site)
            .values(board_theme=theme.value)
        )
        db.commit()
    publish_live(LiveEvent(LiveEventType.BOARD_CONFIG_CHANGED, day.clinic.site))


def _every_state(day: SimpleNamespace) -> None:
    """Five queues: called, a new call, called again, being seen, and one with nobody at all."""
    queues = day.open_queues(5)
    for queue_id in queues[:4]:
        day.issue(queue_id, 3)
    long_ago = now_sast()
    day.call(queues[0], moment=long_ago - timedelta(minutes=5))  # called a while ago
    day.call(queues[1])  # just called
    for index, target in ((2, TicketStatus.RECALLED), (3, TicketStatus.IN_PROGRESS)):
        number = day.call(queues[index], moment=long_ago - timedelta(minutes=5))
        with day.clinic.session() as db:
            ticket = (
                db.query(Ticket)
                .filter(Ticket.number == number, Ticket.queue_id == queues[index])
                .one()
            )
            # Moved a while ago too: a recall that has just happened is itself a new call.
            transition_ticket(
                db,
                ticket.id,
                target,
                actor=_DESK,
                moment=long_ago - timedelta(minutes=4),
            )
            db.commit()


def _open(day: SimpleNamespace, base_url: str | None = None, **options: Any) -> Any:
    """The board, drawn, with its fonts."""
    if base_url:
        options["base_url"] = base_url
    page = day.new_page(1920, 1080, **options)
    page.goto(day.clinic.page_path)
    page.wait_for_selector(".panel")
    page.evaluate("document.fonts.ready.then(() => true)")
    # The stream's first event redraws the board and sets its clock: measure after it.
    _until_live(page)
    return page


def _until_live(page: Any, timeout: float = 30.0) -> None:
    """Wait until the board's stream is open (asked with ``evaluate``, which the page's CSP allows)."""
    deadline = time.monotonic() + timeout
    while (
        page.evaluate(
            "() => window.ClinicQBoardLive && window.ClinicQBoardLive.state().state"
        )
        != "live"
    ):
        if time.monotonic() > deadline:
            raise AssertionError("the board did not go live")
        time.sleep(0.05)


def _until_text(page: Any, selector: str, text: str, timeout: float = 15.0) -> None:
    """Wait until ``selector``'s text is ``text`` (the board's tick runs on its own real-time timer)."""
    deadline = time.monotonic() + timeout
    while (
        page.evaluate("(s) => document.querySelector(s).textContent", selector) != text
    ):
        if time.monotonic() > deadline:
            raise AssertionError(f"{selector} never read {text!r}")
        time.sleep(0.05)


def _shots() -> Path | None:
    """Where to save the evidence screenshots, when asked for."""
    folder = os.environ.get("BOARD_A11Y_SHOTS")
    if not folder:
        return None
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("theme", list(BoardTheme), ids=[t.value for t in BoardTheme])
def test_every_text_on_every_board_state_passes_wcag_aa_as_drawn(
    board_day: SimpleNamespace, theme: BoardTheme
) -> None:
    """All of it at 4.5:1 or more, including the page count, the notice and the reconnecting line."""
    _every_state(board_day)
    _set_theme(board_day, theme)
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()

    page = _open(board_day)
    page.clock.install()
    for shown in ("Page 1 of 2", "Page 2 of 2"):  # the new call's page, then the other
        _until_text(page, "[data-board-pages]", shown)
        for item in page.evaluate(_CONTRAST):
            seen.add(item["text"])
            if item["ratio"] < TEXT_MINIMUM:
                failures.append(item)
        page.clock.fast_forward(
            22_000
        )  # past the highlight, so the page turns at the next tick

    # A server to lose, for the reconnecting line.
    with serve(board_day.clinic.app) as base_url:
        lost = _open(board_day, base_url)
    lost.locator("[data-board-connection]:not([hidden])").wait_for()
    # And the stale banner that follows once nothing has been heard for its time (Issue 62).
    lost.locator("[data-board-stale]:not([hidden])").wait_for(timeout=60_000)
    for item in lost.evaluate(_CONTRAST):
        seen.add(item["text"])
        if item["ratio"] < TEXT_MINIMUM:
            failures.append(item)

    for expected in (
        "Called now",
        "Called again",
        "Being seen",
        "Please come in",
        "—",
        "Nobody waiting",
        "Page 1 of 2",
        "Reconnecting to the clinic…",
        "Not up to date. Last updated at",
    ):
        assert any(expected in text for text in seen), (theme.value, expected)
    assert failures == [], (theme.value, failures)


def test_each_status_has_its_own_words_and_shape_and_the_board_reads_in_greyscale(
    board_day: SimpleNamespace,
) -> None:
    """With colour vision emulated away, statuses still differ by words and shape in every theme."""
    _every_state(board_day)
    folder = _shots()
    for theme in BoardTheme:
        _set_theme(board_day, theme)
        page = _open(board_day)
        cdp = page.context.new_cdp_session(page)
        cdp.send("Emulation.setEmulatedVisionDeficiency", {"type": "achromatopsia"})
        statuses = {item["status"]: item for item in page.evaluate(_STATUSES)}
        assert set(statuses) == {"called", "new", "recalled", "in_progress"}, theme
        words = {item["words"] for item in statuses.values()}
        shapes = {item["shape"] for item in statuses.values()}
        assert len(words) == 4 and len(shapes) == 4, (theme, statuses)
        if folder:
            page.screenshot(path=str(folder / f"board-{theme.value}-greyscale.png"))
            cdp.send("Emulation.setEmulatedVisionDeficiency", {"type": "none"})
            page.screenshot(path=str(folder / f"board-{theme.value}.png"))
            cdp.send("Emulation.setEmulatedVisionDeficiency", {"type": "deuteranopia"})
            page.screenshot(path=str(folder / f"board-{theme.value}-deuteranopia.png"))


def test_under_reduced_motion_the_new_call_stops_pulsing_and_gains_a_still_ring(
    board_day: SimpleNamespace,
) -> None:
    """No animation, and an inset ring the moving version does not need."""
    _every_state(board_day)
    moving = {i["status"]: i for i in _open(board_day).evaluate(_STATUSES)}["new"]
    still = {
        i["status"]: i
        for i in _open(board_day, reduced_motion="reduce").evaluate(_STATUSES)
    }["new"]
    assert moving["animation"] == "kiosk-called"
    assert still["animation"] == "none"
    assert "inset" in still["shadow"] and "inset" not in moving["shadow"]
    assert still["words"] == moving["words"] == "Called now"


def test_a_clinic_changing_its_theme_recolours_open_boards_at_once_without_a_reload(
    board_day: SimpleNamespace,
) -> None:
    """``board.config_changed`` carries the new theme, and the board applies it in place."""
    board_day.open_queues(1)
    _set_theme(board_day, BoardTheme.DIM)
    page = _open(board_day)
    page.evaluate("() => { window.__notReloaded = true; }")
    assert page.locator(".kiosk").get_attribute("data-board-theme") == "dim"
    _set_theme(board_day, BoardTheme.HIGH_CONTRAST)
    page.locator('.kiosk[data-board-theme="high_contrast"]').wait_for()
    assert (
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.kiosk')).backgroundColor"
        )
        == "rgb(0, 0, 0)"
    )
    assert page.evaluate("() => window.__notReloaded === true")
