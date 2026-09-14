"""The board page's route: rendered from the projection, uncached, and honest about a missing board (Issue 56).

The page is drawn by ``board.js`` in a browser (``tests/e2e/display/test_board_page.py`` measures it); here
the route is checked through what it hands the template, never its markup
(``docs/IDE/RULES/testing-strategy.mdc``): the same payload ``/state`` answers, the timings the script
runs on, and the notices, which a platform setting can switch off.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette import status

from src.commons.enums import BoardLanguage, SiteStatus
from src.modules.display.messages import HEALTH_MESSAGES
from src.modules.display.projection import BoardState
from src.web import display as display_routes
from tests.factories import SiteFactory


def test_the_page_carries_exactly_what_the_json_answers_and_the_timings_it_runs_on(
    board: SimpleNamespace,
) -> None:
    """One payload for both: the page's first draw and the poll's answers cannot disagree."""
    board.ticket(board.world.triage)
    client = board.device()
    page = client.get(f"/display/{board.world.site_a}")
    state = client.get(board.state(board.world.site_a)).json()

    assert page.status_code == status.HTTP_200_OK
    assert page.headers["cache-control"] == "no-store"
    assert page.template.name == "display/board.html"  # type: ignore[attr-defined]
    context = page.context  # type: ignore[attr-defined]
    assert isinstance(context["board"], BoardState)
    assert {**context["payload"], "as_of": None} == {**state, "as_of": None}
    assert context["state_url"] == board.state(board.world.site_a)
    assert (
        context["poll_seconds"],
        context["highlight_seconds"],
        context["panels_per_page"],
        context["page_seconds"],
    ) == (
        display_routes.POLL_SECONDS,
        display_routes.HIGHLIGHT_SECONDS,
        display_routes.PANELS_PER_PAGE,
        display_routes.PAGE_SECONDS,
    )
    assert context["messages"] == HEALTH_MESSAGES[BoardLanguage.ENGLISH]
    assert context["message_language"] is BoardLanguage.ENGLISH


def test_the_health_notices_can_be_switched_off_for_the_platform(
    board: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BOARD_HEALTH_TICKER=false``: the page gets no notices to show."""
    settings = board.world.settings.model_copy(update={"board_health_ticker": False})
    monkeypatch.setattr(display_routes, "get_settings", lambda: settings)
    page = board.device().get(f"/display/{board.world.site_a}")
    assert page.status_code == status.HTTP_200_OK
    assert page.context["messages"] == ()  # type: ignore[attr-defined]


def test_a_board_that_cannot_be_shown_says_so_to_its_own_screen_and_everyone_else_is_sent_to_pair(
    board: SimpleNamespace,
) -> None:
    """A screen whose clinic is not verified gets the plain 404 page; a browser that is no clinic's screen is sent to /display."""
    with board.session() as db:
        draft = SiteFactory.create(db, name="Draft Clinic", status=SiteStatus.DRAFT)
        db.commit()
        draft_id = draft.id
    page = board.device(site_id=draft_id).get(f"/display/{draft_id}")
    assert page.status_code == status.HTTP_404_NOT_FOUND
    assert page.template.name == "display/unavailable.html"  # type: ignore[attr-defined]
    assert "board" not in page.context  # type: ignore[attr-defined]

    anyone = board.world.anonymous()
    for site_id in (
        board.world.site_a,
        draft_id,
        "0199b0c0-0000-7000-8000-00000000dead",
    ):
        sent = anyone.get(f"/display/{site_id}")
        assert sent.status_code == status.HTTP_302_FOUND
        assert sent.headers["location"] == "/display"


def test_only_a_paired_box_is_given_the_offline_worker_and_the_worker_carries_the_release(
    board: SimpleNamespace,
) -> None:
    """A box's board registers the worker (Issue 62); a manager's preview keeps nothing offline.

    The worker is served under /display so it may look after the start page, with this release's version
    written in (a new release installs a new worker) and never from the browser's HTTP cache.
    """
    page = f"/display/{board.world.site_a}"
    box = board.device().get(page).context  # type: ignore[attr-defined]
    staff = board.world.client("manager.a").get(page).context  # type: ignore[attr-defined]

    assert box["offline_worker_url"] == "/display/board-sw.js"
    assert staff["offline_worker_url"] == ""
    assert (box["stale_after_seconds"], box["stale_limit_seconds"]) == (
        display_routes.STALE_AFTER_SECONDS,
        display_routes.STALE_LIMIT_SECONDS,
    )

    worker = board.world.anonymous().get("/display/board-sw.js")
    assert worker.status_code == status.HTTP_200_OK
    assert worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert worker.headers["service-worker-allowed"] == "/display"
    assert display_routes.BOARD_WORKER_VERSION_MARK not in worker.text
    assert f"var VERSION = '{display_routes.get_settings().version}';" in worker.text
