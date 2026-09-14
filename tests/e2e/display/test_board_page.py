"""The waiting-room board in Chromium: layouts, legibility, the new-call highlight and a day's run (Issue 56).

What only a browser can show, measured rather than looked at:

* **Layouts.** One, two, three and five open queues, at 1080p and at 720p: the grid takes the right shape,
  nothing scrolls or spills out of a panel, the pointer is hidden, and five queues show four at a time
  with a page count.
* **Legibility on a 32-inch screen.** The cap height of every number is measured with the browser's own
  text metrics and converted to millimetres on a 32-inch 16:9 panel (398.5 mm tall). A number being
  served must be at least 49 mm (ADA 2010 §703.5.5 for 5 m) and a number up next at least 21.8 mm (what
  a 6/18 eye resolves at 5 m). ``docs/OPS/BOARD_LEGIBILITY.md`` explains these numbers and the check
  standing in the room.
* **A new call.** Unmistakable with motion and without: under ``prefers-reduced-motion`` the pulse stops
  and the inverted colours, heavy border and "Called now" stay.
* **A day.** Eight hours on the page's own clock, with a call every three minutes: the heap, the DOM
  and the layout end where they started.

The page's timers run on Playwright's clock where time matters, so no test waits for real minutes.
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.commons.time import now_sast
from src.web.display import HIGHLIGHT_SECONDS, PAGE_SECONDS, POLL_SECONDS

pytestmark = pytest.mark.postgres

#: A 32-inch 16:9 panel is 398.5 mm tall.
SCREEN_HEIGHT_MM = 32 * 25.4 * 9 / (16**2 + 9**2) ** 0.5
#: ADA 2010 §703.5.5: characters read from 5 m are at least 49 mm tall (1.925 in).
SERVING_CAP_MIN_MM = 49.0
#: A 6/18 letter subtends 15 minutes of arc: 21.8 mm at 5 m.
UP_NEXT_CAP_MIN_MM = 21.8
VIEWPORTS = ((1920, 1080), (1280, 720))

_MEASURE = """() => {
  const context = document.createElement('canvas').getContext('2d');
  const cap = (el) => {
    const style = getComputedStyle(el);
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    return context.measureText('H').actualBoundingBoxAscent;
  };
  const panels = [...document.querySelectorAll('.panel')];
  const spills = panels.filter((panel) => {
    const box = panel.getBoundingClientRect();
    return [...panel.querySelectorAll('.serving, .serving-state, .panel-also, .next, .panel-waiting')].some((child) => {
      const r = child.getBoundingClientRect();
      if (!r.width && !r.height) return false; // not displayed
      return r.right > box.right + 1 || r.bottom > box.bottom + 1 || r.left < box.left - 1;
    });
  }).length;
  const pager = document.querySelector('[data-board-pages]');
  return {
    layout: document.querySelector('[data-board-grid]').dataset.layout,
    panels: panels.length,
    servingCapPx: Math.min(...[...document.querySelectorAll('.serving-number')].map(cap)),
    nextCapPx: Math.min(...[...document.querySelectorAll('.next-number')].map(cap)),
    spilling: spills,
    scrolls: document.documentElement.scrollHeight > innerHeight
      || document.documentElement.scrollWidth > innerWidth,
    cursor: getComputedStyle(document.body).cursor,
    pager: pager.hidden ? null : pager.textContent,
  };
}"""


#: Whether a ticket number is on the screen.
_SHOWS_NUMBER = "(number) => [...document.querySelectorAll('.serving-number')].some((el) => el.textContent === number)"


def _until(
    page: Any, predicate: str, argument: Any = None, timeout: float = 30.0
) -> None:
    """Wait until ``predicate`` is true in the page, asking it with ``evaluate``.

    The day's run waits this way rather than with locators: a Playwright locator keeps the elements it
    matched reachable from its injected script, which on its own grows the page's node count by a dozen
    nodes a call and would make a leak-free board look leaky. ``evaluate`` holds nothing.
    """
    deadline = time.monotonic() + timeout
    while not page.evaluate(predicate, argument):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {predicate} ({argument!r})")
        time.sleep(0.02)


def _open(
    day: SimpleNamespace, width: int = 1920, height: int = 1080, **options: Any
) -> Any:
    """The board page, with its fonts loaded and its panels drawn."""
    page = day.new_page(width, height, **options)
    page.goto(day.clinic.page_path)
    page.wait_for_selector(".panel")
    page.evaluate("document.fonts.ready.then(() => true)")
    return page


@pytest.mark.parametrize(
    ("open_queues", "layout", "panels", "pager"),
    [
        (1, "1", 1, None),
        (2, "2", 2, None),
        (3, "3", 3, None),
        (5, "4", 4, "Page 1 of 2"),
    ],
    ids=["one-queue", "two-queues", "three-queues", "five-queues"],
)
def test_the_board_lays_out_one_two_three_and_five_queues_legibly_at_1080p_and_720p(
    board_day: SimpleNamespace,
    open_queues: int,
    layout: str,
    panels: int,
    pager: str | None,
) -> None:
    """Each layout fits the screen at both resolutions, with numbers large enough for 5 m on 32 inches."""
    for queue_id in board_day.open_queues(open_queues):
        board_day.issue(queue_id, 7)
    # Calls long enough ago not to be highlighted, so page one stays up for the measurement.
    earlier = now_sast() - timedelta(seconds=HIGHLIGHT_SECONDS * 3)
    # Three calls each, so every panel also carries its "Also called" line: the tallest a panel gets.
    for queue_id in board_day.clinic.queues[:open_queues]:
        for _ in range(3):
            board_day.call(queue_id, moment=earlier)
    for width, height in VIEWPORTS:
        page = _open(board_day, width, height)
        measured = page.evaluate(_MEASURE)
        mm_per_px = SCREEN_HEIGHT_MM / height
        assert measured["layout"] == layout, (width, measured)
        assert measured["panels"] == panels, (width, measured)
        assert measured["pager"] == pager, (width, measured)
        assert measured["spilling"] == 0, (width, measured)
        assert measured["scrolls"] is False, (width, measured)
        assert measured["cursor"] == "none", (width, measured)
        assert measured["servingCapPx"] * mm_per_px >= SERVING_CAP_MIN_MM, (
            width,
            measured,
        )
        assert measured["nextCapPx"] * mm_per_px >= UP_NEXT_CAP_MIN_MM, (
            width,
            measured,
        )


def test_a_new_call_is_unmistakable_with_motion_and_just_as_clear_without_it(
    board_day: SimpleNamespace,
) -> None:
    """The highlight pulses, and under reduced motion keeps its colours, border and words with no pulse."""
    triage, *_ = board_day.open_queues(2)
    board_day.issue(triage, 3)
    number = board_day.call(triage)

    looks: dict[str, dict[str, Any]] = {}
    for motion in ("no-preference", "reduce"):
        page = _open(board_day, reduced_motion=motion)
        called = page.locator(".serving.is-new")
        assert called.locator(".serving-number").text_content() == number
        assert called.locator(".serving-state").text_content() == "Called now"
        looks[motion] = called.evaluate(
            """(el) => { const s = getComputedStyle(el);
                return { animation: s.animationName, background: s.backgroundColor,
                         border: s.borderTopWidth, color: s.color }; }"""
        )
    assert looks["no-preference"]["animation"] == "kiosk-called"
    assert looks["reduce"]["animation"] == "none"
    for prop in ("background", "border", "color"):
        assert looks["reduce"][prop] == looks["no-preference"][prop], prop
    assert looks["reduce"]["background"] == "rgb(255, 216, 77)"


def test_the_highlight_ends_and_the_board_picks_up_the_next_call_on_its_own(
    board_day: SimpleNamespace,
) -> None:
    """No reload: the poll brings the next call, and the highlight fades on the server's clock."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 3)
    first = board_day.call(triage)
    page = _open(board_day)
    page.clock.install()
    assert page.locator(".serving.is-new .serving-number").text_content() == first

    page.clock.fast_forward(int((HIGHLIGHT_SECONDS + 1) * 1000))
    _until(page, "() => !document.querySelector('.serving.is-new')")

    second = board_day.call(triage)
    page.clock.fast_forward(int((POLL_SECONDS + 1) * 1000))
    page.locator(".serving.is-new .serving-number", has_text=second).wait_for()
    assert page.locator(".panel-also").text_content() == f"Also called: {first}"


def test_more_queues_than_fit_turn_pages_and_a_new_call_brings_its_page_forward(
    board_day: SimpleNamespace,
) -> None:
    """Five queues: four panels, then the fifth; a call in the fifth shows its page at once."""
    queues = board_day.open_queues(5)
    for queue_id in queues:
        board_day.issue(queue_id, 2)
    page = _open(board_day)
    page.clock.install()
    pager = page.locator("[data-board-pages]")
    assert pager.text_content() == "Page 1 of 2"
    assert page.locator(".panel").count() == 4

    page.clock.fast_forward(int((PAGE_SECONDS + 1) * 1000))
    page.locator("[data-board-pages]", has_text="Page 2 of 2").wait_for()
    assert page.locator(".panel .panel-label").all_text_contents() == [
        "Family planning"
    ]

    page.clock.fast_forward(int((PAGE_SECONDS + 1) * 1000))
    page.locator("[data-board-pages]", has_text="Page 1 of 2").wait_for()

    called = board_day.call(queues[4])
    page.clock.fast_forward(int((POLL_SECONDS + 1) * 1000))
    page.locator(".serving.is-new .serving-number", has_text=called).wait_for()
    assert pager.text_content() == "Page 2 of 2"


def test_eight_hours_of_calls_leave_the_heap_the_dom_and_the_layout_where_they_started(
    board_day: SimpleNamespace,
) -> None:
    """A clinic day on the page's clock: a call every three minutes for eight hours, measured after GC."""
    queues = board_day.open_queues(4)
    for queue_id in queues:
        board_day.issue(queue_id, 4)
    page = _open(board_day)
    page.clock.install()
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")

    def snapshot() -> dict[str, float]:
        cdp.send("HeapProfiler.collectGarbage")
        cdp.send("HeapProfiler.collectGarbage")
        metrics = {
            m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]
        }
        rects = page.evaluate(
            "() => [...document.querySelectorAll('.panel')].map((p) => {"
            " const r = p.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })"
        )
        return {
            "attached": page.evaluate(
                "() => document.getElementsByTagName('*').length"
            ),
            "heap": metrics["JSHeapUsedSize"],
            "nodes": metrics["Nodes"],
            "listeners": metrics["JSEventListeners"],
            "rects": rects,
        }

    def one_call(index: int) -> None:
        """Keep the waiting line the same length: a new arrival, then a call that sees off the last."""
        queue_id = queues[index % len(queues)]
        board_day.issue(queue_id, 1)
        number = board_day.call(queue_id, finish_previous=True)
        page.clock.fast_forward(int((POLL_SECONDS + 1) * 1000))
        _until(page, _SHOWS_NUMBER, number)
        # The rest of the three minutes: every tick, highlight change and notice change in it.
        page.clock.run_for(180_000 - int((POLL_SECONDS + 1) * 1000))

    for index in range(10):  # warm up: the first half hour
        one_call(index)
    before = snapshot()
    calls = 8 * 60 // 3
    for index in range(calls):
        one_call(index)
    after = snapshot()

    print(f"\n8 h, {calls} calls: {before} -> {after}")  # noqa: T201 — the figures the PR quotes
    assert after["heap"] <= before["heap"] * 1.10 + 512 * 1024, (before, after)
    # Nodes include detached ones the collector has not reclaimed yet, so only growth is a finding.
    assert after["nodes"] <= before["nodes"] * 1.05, (before, after)
    assert after["attached"] == before["attached"], (before, after)
    assert after["listeners"] <= before["listeners"], (before, after)
    assert after["rects"] == before["rects"]
