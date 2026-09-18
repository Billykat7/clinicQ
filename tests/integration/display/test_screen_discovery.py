"""Screens the server finds on its own network, and the board it sends them (Issue 237).

The other way to set a waiting-room board up. The ordinary one is a code: a box shows six characters
and a manager carries them to the dashboard (``test_display_devices.py``). This one runs the other
way round — the server asks its network what screens are there, a manager picks one, and the code
travels to the screen over the clinic's own network instead of through a person.

The network itself is stubbed, and deliberately so: what these tests are about is the *handover* —
which grant may search, what a manager is told when nothing answers, what row exists while a screen
is thinking about it, and the one-time code that makes the screen this clinic's screen. A real
Chromecast answering a real multicast query is not something a test suite can hold still, and
pretending otherwise would prove nothing about any of that.

What this proves:

* searching and connecting need the grant that pairs a screen; the front desk's read grant does not
  open either, and neither works across clinics;
* a deployment with the feature off says so in a sentence rather than answering an empty list;
* connecting holds a real row for the screen **before** it is contacted, so a screen that never
  answers is visible in the clinic's list and removable, and the attempt is audited either way;
* the screen becomes this clinic's screen by handing its code back — once, and only once — and is
  given the same httpOnly device cookie a box gets, and its clinic's board;
* a code minted for a screen the server reached cannot be spent on the code-typing flow, or the
  other way round.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.commons.enums import AuditEntityType, DisplayDeviceStatus
from src.database.models import AuditEvent, DisplayDevice
from src.modules.display import casting, devices, discovery
from src.modules.display.router import discovery as router_discovery

SCREEN = discovery.DiscoveredScreen(
    uuid="ec9b8350-bd0d-f7e8-9faa-ace4909c455f",
    name="Smart TV Pro",
    model="Smart TV Pro",
    address="192.168.3.106",
    port=8009,
    manufacturer="Generic",
)


def _discover(board: SimpleNamespace, who: str = "manager.a", site: str | None = None):  # type: ignore[no-untyped-def]
    """Search the server's network as ``who``, for clinic A unless another is named."""
    site_id = site or board.world.site_a
    return board.world.client(who).post(
        f"/api/v1/sites/{site_id}/display-devices/discover"
    )


def _connect(
    board: SimpleNamespace,
    who: str = "manager.a",
    site: str | None = None,
    **extra: object,
):  # type: ignore[no-untyped-def]
    """Send clinic A's board to the screen at SCREEN's address, as ``who``."""
    site_id = site or board.world.site_a
    return board.world.client(who).post(
        f"/api/v1/sites/{site_id}/display-devices/connect",
        json={
            "address": SCREEN.address,
            "port": SCREEN.port,
            "name": SCREEN.name,
            "uuid": SCREEN.uuid,
            "label": "TV by reception",
            **extra,
        },
    )


@pytest.fixture
def found(monkeypatch: pytest.MonkeyPatch) -> list[discovery.DiscoveredScreen]:
    """Make the network answer with whatever the test puts in the returned list."""
    screens: list[discovery.DiscoveredScreen] = [SCREEN]

    def _scan(**_: object) -> discovery.ScanOutcome:
        return discovery.ScanOutcome(screens=tuple(screens))

    monkeypatch.setattr(router_discovery, "scan_for_screens", _scan)
    return screens


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record every cast the routes attempt, and let the screen accept it."""
    casts: list[dict[str, object]] = []

    def _send(target: discovery.ScreenTarget, **kwargs: object) -> casting.CastOutcome:
        casts.append({"address": target.address, **kwargs})
        return casting.CastOutcome(
            reached=True, showing=True, note="Opening.", screen_name=target.name
        )

    monkeypatch.setattr(casting, "send_board_to_screen", _send)
    return casts


# --- Who may look, and where ---------------------------------------------------


def test_the_front_desk_may_not_search_or_take_over_a_screen(
    board: SimpleNamespace, found: list[discovery.DiscoveredScreen]
) -> None:
    """Both need the grant that pairs a screen; reading the list of screens is not enough.

    What a search returns is the list a person picks from to put something on a waiting-room wall,
    so it is gated where the taking-over is, not where the reading is.
    """
    assert _discover(board, "desk.a").status_code == status.HTTP_403_FORBIDDEN
    assert _connect(board, "desk.a").status_code == status.HTTP_403_FORBIDDEN


def test_staff_of_another_clinic_may_not_take_over_a_screen_here(
    board: SimpleNamespace,
    found: list[discovery.DiscoveredScreen],
    sent: list[dict[str, object]],
) -> None:
    """Clinic B's front desk holds nothing at clinic A, and the screen is never contacted."""
    refused = _connect(board, "desk.b", site=board.world.site_a)

    assert refused.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )
    assert sent == []


# --- What a manager is told -----------------------------------------------------


def test_a_deployment_with_the_feature_off_says_so_rather_than_answering_nothing(
    board: SimpleNamespace,
) -> None:
    """The real scanner runs here, and answers the only useful thing it can: this is switched off.

    A cloud instance shares no network with any clinic, so an empty list would be the truth and
    still useless — the manager cannot tell it from a television that is asleep.
    """
    answer = _discover(board)

    assert answer.status_code == status.HTTP_200_OK, answer.text
    body = answer.json()
    assert body["total"] == 0 and body["items"] == []
    assert body["searched"] is False
    assert body["note"] == discovery.OFF_NOTE


def test_a_search_reports_the_screens_that_answered(
    board: SimpleNamespace, found: list[discovery.DiscoveredScreen]
) -> None:
    """Each screen comes back with a name to offer and the address the manager's choice sends back."""
    answer = _discover(board)

    assert answer.status_code == status.HTTP_200_OK, answer.text
    body = answer.json()
    assert body["total"] == 1 and body["note"] == ""
    only = body["items"][0]
    assert only["address"] == SCREEN.address
    assert only["suggested_label"] == "Smart TV Pro"
    assert only["uuid"] == SCREEN.uuid


# --- Taking a screen over -------------------------------------------------------


def test_connecting_holds_a_row_before_the_screen_is_asked_and_audits_either_way(
    board: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A screen that never answers still leaves a visible, removable row and an audit trail.

    The two answers a manager must be able to tell apart — "the television is off" and "the board is
    up" — both belong in the clinic's list. A failed attempt that left nothing behind would look
    exactly like one that was never made.
    """

    def _silent(target: discovery.ScreenTarget, **_: object) -> casting.CastOutcome:
        return casting.CastOutcome(
            reached=False, showing=False, note=casting.UNREACHABLE_NOTE
        )

    monkeypatch.setattr(casting, "send_board_to_screen", _silent)

    answer = _connect(board)

    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    body = answer.json()
    assert body["reached"] is False and body["showing"] is False
    assert body["note"] == casting.UNREACHABLE_NOTE
    # Held, not paired: the screen has not taken it up, so it reads as being set up.
    assert body["device"]["status"] == DisplayDeviceStatus.PAIRING.value
    assert body["device"]["site_id"] == board.world.site_a

    listed = board.world.client("manager.a").get(
        f"/api/v1/sites/{board.world.site_a}/display-devices"
    )
    assert [row["id"] for row in listed.json()["items"]] == [body["device"]["id"]]

    with board.session() as db:
        audit = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.DISPLAY_DEVICE.value
            )
        ).scalar_one()
        assert SCREEN.address in audit.context
        assert audit.actor == "manager.a@clinicq.example"


def test_the_screen_claims_its_row_and_opens_its_clinics_board(
    board: SimpleNamespace, sent: list[dict[str, object]]
) -> None:
    """The whole handover: the manager picks a screen, the screen hands the code back, and it is paired.

    Nobody types anything. The code the dashboard sent over the network is what proves the screen
    asking is the screen that was chosen.
    """
    answer = _connect(board)
    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    assert answer.json()["showing"] is True
    code = str(sent[0]["claim_code"])

    screen = TestClient(board.world.app, follow_redirects=False)
    claimed = screen.post("/display/claim", json={"code": code})

    assert claimed.status_code == status.HTTP_200_OK, claimed.text
    assert claimed.json() == {"board_url": f"/display/{board.world.site_a}"}
    # The same cookie a box plugged into the TV would get, on the same terms.
    cookie = claimed.headers["set-cookie"]
    assert "clinicq_display=" in cookie and "HttpOnly" in cookie
    assert "SameSite=strict" in cookie and "Path=/display" in cookie

    # It is now this clinic's screen: /display takes it straight to the board.
    opened = screen.get("/display")
    assert opened.status_code == status.HTTP_302_FOUND
    assert opened.headers["location"] == f"/display/{board.world.site_a}"
    assert screen.get(board.state(board.world.site_a)).status_code == status.HTTP_200_OK

    row = answer.json()["device"]["id"]
    listed = board.world.client("manager.a").get(
        f"/api/v1/sites/{board.world.site_a}/display-devices"
    )
    shown = next(item for item in listed.json()["items"] if item["id"] == row)
    assert shown["status"] == DisplayDeviceStatus.ONLINE.value
    assert shown["label"] == "TV by reception"


def test_a_claim_code_works_once(
    board: SimpleNamespace, sent: list[dict[str, object]]
) -> None:
    """Spent the moment it is used, so a replay — or a second screen — finds nothing waiting."""
    _connect(board)
    code = str(sent[0]["claim_code"])
    first = TestClient(board.world.app, follow_redirects=False)
    assert first.post("/display/claim", json={"code": code}).status_code == 200

    second = TestClient(board.world.app, follow_redirects=False)
    replay = second.post("/display/claim", json={"code": code})

    assert replay.status_code == status.HTTP_404_NOT_FOUND
    assert "set-cookie" not in replay.headers


def test_the_two_kinds_of_code_cannot_be_spent_on_each_other(
    board: SimpleNamespace, sent: list[dict[str, object]]
) -> None:
    """A code held for a chosen screen is not a code a person may type, and vice versa.

    The two flows mint codes that look alike and mean opposite things: one belongs to a box that no
    clinic owns yet, the other to a row a clinic already owns. Letting either be spent as the other
    would let a manager pair a screen the server chose, or a stranger's box claim a held row.
    """
    _connect(board)
    held = str(sent[0]["claim_code"])

    typed = board.world.client("manager.a").post(
        f"/api/v1/sites/{board.world.site_a}/display-devices",
        json={"pairing_code": held},
    )
    assert typed.status_code == status.HTTP_404_NOT_FOUND

    box = TestClient(board.world.app, follow_redirects=False)
    shown = str(box.get("/display").context["code"])  # type: ignore[attr-defined]
    claimed = box.post("/display/claim", json={"code": shown})
    assert claimed.status_code == status.HTTP_404_NOT_FOUND

    # Neither attempt changed anything: the held row is still waiting, the box still unpaired.
    with board.session() as db:
        rows = db.execute(select(DisplayDevice)).scalars().all()
        assert sorted(devices.status_of(row).value for row in rows) == [
            DisplayDeviceStatus.PAIRING.value,
            DisplayDeviceStatus.PAIRING.value,
        ]


def test_a_screen_with_a_browser_can_claim_by_opening_one_address(
    board: SimpleNamespace, sent: list[dict[str, object]]
) -> None:
    """The link form of the same claim, for a television that will not be cast to but can browse.

    Same single-use code, same cookie, same board — the difference is only that the screen was sent
    to the address instead of being handed the code over a Cast connection.
    """
    _connect(board)
    code = str(sent[0]["claim_code"])

    screen = TestClient(board.world.app, follow_redirects=False)
    opened = screen.get(f"/display/claim?code={code}")

    assert opened.status_code == status.HTTP_302_FOUND
    assert opened.headers["location"] == f"/display/{board.world.site_a}"
    assert "clinicq_display=" in opened.headers["set-cookie"]

    # A code that has been spent sends the next screen to the ordinary pairing page, not an error.
    again = TestClient(board.world.app, follow_redirects=False).get(
        f"/display/claim?code={code}"
    )
    assert again.status_code == status.HTTP_302_FOUND
    assert again.headers["location"] == "/display"


def test_the_cast_receiver_page_is_served_with_its_own_policy(
    board: SimpleNamespace,
) -> None:
    """The one page allowed a third-party script, and only the one it cannot do without.

    A Chromecast will not run a receiver that does not load Google's receiver SDK, so this page's
    policy names that host — and nothing else changes: every other page keeps the strict policy,
    which is what the exception in the security-headers middleware is worth checking.
    """
    page = TestClient(board.world.app).get("/display/cast")

    assert page.status_code == status.HTTP_200_OK
    policy = page.headers["content-security-policy"]
    assert "https://www.gstatic.com" in policy
    assert "object-src 'none'" in policy and "frame-ancestors 'self'" in policy

    ordinary = TestClient(board.world.app).get("/display")
    assert "gstatic" not in ordinary.headers["content-security-policy"]


# --- What the manager's page offers ----------------------------------------------


def test_the_page_offers_the_search_only_where_it_could_work(
    board: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The **Screens on this network** section is rendered only by a deployment that can search.

    A cloud instance shares no network with any clinic, so the button could never do anything there;
    offering it would be a promise the deployment cannot keep. The page is told by ``can_find_screens``
    and the template renders nothing when it is false.
    """
    from src.web.dashboard import settings as settings_page

    page = f"/dashboard/sites/{board.world.site_a}/settings/devices"

    # Both states are pinned rather than left to the ambient default: this page reads its settings
    # through the module-level ``get_settings``, so a developer who has switched the feature on in
    # their own ``.env`` would otherwise decide what this test proves.
    def _with(enabled: bool) -> None:
        pinned = board.world.settings.model_copy(
            update={"smart_tv_discovery_enabled": enabled}
        )
        monkeypatch.setattr(settings_page, "get_settings", lambda: pinned)

    _with(False)
    off = board.world.client("manager.a").get(page)
    assert off.status_code == status.HTTP_200_OK
    assert off.context["can_find_screens"] is False  # type: ignore[attr-defined]

    _with(True)
    on = board.world.client("manager.a").get(page)
    assert on.status_code == status.HTTP_200_OK
    assert on.context["can_find_screens"] is True  # type: ignore[attr-defined]


def test_a_screen_is_never_told_to_claim_itself(
    board: SimpleNamespace, sent: list[dict[str, object]]
) -> None:
    """A manager working on the server reaches it at ``localhost``; a television cannot.

    Handing the screen that address would send it to ask itself. Nothing is sent instead, and the
    receiver falls back to the address it was served from — the registered receiver URL, which is by
    definition one the screen could reach.
    """
    _connect(board)

    # The test client calls the dashboard as http://testserver, which is not loopback…
    assert sent[0]["board_url"] == "http://testserver/display/claim"

    sent.clear()
    board.world.client("manager.a").post(
        f"/api/v1/sites/{board.world.site_a}/display-devices/connect",
        json={"address": SCREEN.address, "label": "On the server itself"},
        headers={"Host": "localhost:8062"},
    )

    assert sent[0]["board_url"] == ""
