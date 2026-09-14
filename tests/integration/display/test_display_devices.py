"""Kiosk boxes over HTTP: pairing with a code, showing only their clinic's board, removal and silence (Issue 61).

Driven the way a box and a manager drive it, as two clients: the box opens ``/display`` and reads its code
from what the page was given (never HTML, ``docs/IDE/RULES/testing-strategy.mdc``); the manager types the
code into the API. What this proves:

* a fresh box gets a code and a device cookie (httpOnly, same-site only) and no address to type; once a
  manager pairs it, it finds out by itself and opens its board, under the clinic's own display mode;
* the secret is stored only as its SHA-256, and a wrong, expired or used code all get the same 404;
* a box shows only its own clinic's board, and only the queues it was given;
* a removed box is refused at once (board, JSON, heartbeat) and is offered a new code;
* the heartbeat keeps a box's record current, and refuses a request from another site;
* a box silent for ten minutes raises exactly one alert naming it and its clinic, and one more when back;
* the operator sees every screen on the platform with its status; a clinic manager does not.
"""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    AuditEntityType,
    ConsentPurpose,
    DisplayDeviceStatus,
    DisplayMode,
    TeamWebhookKind,
)
from src.commons.time import now_sast
from src.core import team_alerts
from src.core.security import hash_refresh_token
from src.database.models import AuditEvent, DisplayDevice
from src.modules.display import devices
from tests.integration.display.conftest import NOMVULA_LITE


def _box(board: SimpleNamespace) -> TestClient:
    """A browser that has never been a screen."""
    return TestClient(board.world.app, follow_redirects=False)


def _code(box: TestClient) -> str:
    """Open the start page as a box and read the code it was given to show."""
    page = box.get("/display")
    assert page.status_code == status.HTTP_200_OK, page.text
    assert page.template.name == "display/pair.html"  # type: ignore[attr-defined]
    return str(page.context["code"])  # type: ignore[attr-defined]


def _pair(board: SimpleNamespace, code: str, **extra: object):  # type: ignore[no-untyped-def]
    """A clinic manager pairs the screen showing ``code`` with clinic A."""
    return board.world.client("manager.a").post(
        f"/api/v1/sites/{board.world.site_a}/display-devices",
        json={"pairing_code": code, **extra},
    )


def test_a_fresh_box_pairs_with_a_code_typed_by_the_manager_and_opens_its_board_by_itself(
    board: SimpleNamespace,
) -> None:
    """The whole flow: a code on the box, the manager types it (loosely), the box learns it is paired."""
    nomvula = board.patient()
    board.consent(nomvula, ConsentPurpose.DISPLAY_NAME, True)
    board.ticket(board.world.triage, nomvula)
    board.display(DisplayMode.NAME_LITE)

    box = _box(board)
    start = box.get("/display")
    cookie = start.headers["set-cookie"]
    assert "clinicq_display=" in cookie and "HttpOnly" in cookie
    assert "SameSite=strict" in cookie and "Path=/display" in cookie
    code = str(start.context["code"])  # type: ignore[attr-defined]
    # The screen says how long its code works, and it is the setting the server enforces.
    assert start.context["code_minutes"] == 10  # type: ignore[attr-defined]
    assert box.get("/display/pairing").json() == {"state": "waiting"}

    # What a person types: lower case, a dash instead of the space.
    typed = code.replace(" ", "-").lower()
    paired = _pair(board, typed, label="TV by reception")
    assert paired.status_code == status.HTTP_201_CREATED, paired.text
    assert paired.json()["status"] == DisplayDeviceStatus.ONLINE.value
    assert paired.json()["label"] == "TV by reception"

    assert box.get("/display/pairing").json() == {
        "state": "paired",
        "board_url": f"/display/{board.world.site_a}",
    }
    opened = box.get("/display")
    assert opened.status_code == status.HTTP_302_FOUND
    assert opened.headers["location"] == f"/display/{board.world.site_a}"
    shown = box.get(board.state(board.world.site_a)).json()
    # The clinic's own screen: its own display mode, so the name it agreed to show.
    assert NOMVULA_LITE in json.dumps(shown)

    with board.session() as db:
        audit = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.DISPLAY_DEVICE.value
            )
        ).scalar_one()
        assert audit.actor == "manager.a@clinicq.example"
        assert audit.context == "display board paired: TV by reception"


def test_the_secret_is_stored_only_as_its_digest_and_a_bad_code_learns_nothing(
    board: SimpleNamespace,
) -> None:
    """No column holds the secret or the code; a wrong, a used and an expired code get one answer."""
    box = _box(board)
    code = _code(box)
    secret = box.cookies.get(board.world.settings.display_device_cookie_name)
    with board.session() as db:
        row = db.execute(select(DisplayDevice)).scalar_one()
        assert row.token_hash == hash_refresh_token(secret)
        assert secret not in json.dumps(
            {c.name: str(getattr(row, c.name)) for c in DisplayDevice.__table__.columns}
        )

    wrong = _pair(board, "ZZZ ZZZ")
    assert _pair(board, code).status_code == status.HTTP_201_CREATED
    used = _pair(board, code)
    other = _box(board)
    expired_code = _code(other)
    with board.session() as db:
        pending = db.execute(
            select(DisplayDevice).where(DisplayDevice.site_id.is_(None))
        ).scalar_one()
        pending.pairing_expires_at = now_sast() - timedelta(seconds=1)
        db.commit()
    expired = _pair(board, expired_code)
    assert other.get("/display/pairing").json() == {"state": "expired"}
    assert (
        wrong.status_code
        == used.status_code
        == expired.status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert wrong.json()["detail"] == used.json()["detail"] == expired.json()["detail"]

    receptionist = board.world.client("desk.a").post(
        f"/api/v1/sites/{board.world.site_a}/display-devices",
        json={"pairing_code": _code(_box(board))},
    )
    assert receptionist.status_code == status.HTTP_403_FORBIDDEN


def test_a_box_shows_only_its_own_clinics_board_and_only_the_queues_it_was_given(
    board: SimpleNamespace,
) -> None:
    """The pharmacy's screen shows the pharmacy; clinic B's board is not this box's to see."""
    box = _box(board)
    paired = _pair(board, _code(box), queue_ids=[board.world.pharmacy])
    assert paired.json()["queue_ids"] == [board.world.pharmacy]
    body = box.get(board.state(board.world.site_a)).json()
    assert [q["id"] for q in body["queues"]] == [board.world.pharmacy]
    assert (
        box.get(board.state(board.world.site_b)).status_code
        == status.HTTP_401_UNAUTHORIZED
    )

    # Another clinic's queue cannot be chosen.
    refused = _pair(board, _code(_box(board)), queue_ids=[board.world.other_triage])
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_a_removed_box_is_refused_at_once_and_offered_a_new_code(
    board: SimpleNamespace,
) -> None:
    """Revoke: the board sends it back to pairing, its JSON and heartbeat are 401, and it gets a new code."""
    box = _box(board)
    device_id = _pair(board, _code(box)).json()["id"]
    manager = board.world.client("manager.a")
    board_path = f"/display/{board.world.site_a}"
    assert box.get(board_path).status_code == status.HTTP_200_OK

    removed = manager.post(
        f"/api/v1/sites/{board.world.site_a}/display-devices/{device_id}/revoke"
    )
    assert removed.status_code == status.HTTP_200_OK
    assert removed.json()["status"] == DisplayDeviceStatus.REVOKED.value
    assert box.get(board_path).headers["location"] == "/display"
    assert (
        box.get(board.state(board.world.site_a)).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert (
        box.post("/display/heartbeat", json={}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    again = box.get("/display")
    assert again.template.name == "display/pair.html"  # type: ignore[attr-defined]
    assert (
        "clinicq_display=" in again.headers["set-cookie"]
    )  # a new device, a new secret

    # Removing it twice changes nothing and writes one audit row.
    manager.post(
        f"/api/v1/sites/{board.world.site_a}/display-devices/{device_id}/revoke"
    )
    with board.session() as db:
        rows = db.execute(
            select(AuditEvent).where(AuditEvent.context.like("display board removed%"))
        ).all()
    assert len(rows) == 1


def test_the_heartbeat_keeps_the_record_current_and_refuses_another_site(
    board: SimpleNamespace,
) -> None:
    """A beat records the time, the version and the browser; a cross-site beat is refused."""
    box = _box(board)
    _pair(board, _code(box))
    beat = box.post(
        "/display/heartbeat",
        json={"app_version": "0.8.0"},
        headers={"user-agent": "Mozilla/5.0 (X11; Linux aarch64) Chromium kiosk"},
    )
    assert beat.status_code == status.HTTP_200_OK
    assert beat.json()["state"] == "paired"
    assert "clinicq_display=" in beat.headers["set-cookie"]  # renewed
    with board.session() as db:
        row = db.execute(select(DisplayDevice)).scalar_one()
        assert row.app_version == "0.8.0"
        assert "aarch64" in (row.user_agent or "")
    cross = box.post(
        "/display/heartbeat", json={}, headers={"sec-fetch-site": "cross-site"}
    )
    assert cross.status_code == status.HTTP_403_FORBIDDEN
    assert (
        _box(board).post("/display/heartbeat", json={}).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_a_box_silent_for_ten_minutes_raises_one_alert_naming_it_and_its_clinic_and_one_when_back(
    board: SimpleNamespace,
) -> None:
    """The watch alerts once per silence, with the label and the clinic; the next beat ends it."""
    box = _box(board)
    _pair(board, _code(box), label="TV by reception")
    sent: list[str] = []
    silent_minutes = board.world.settings.display_device_silent_minutes

    def last_heard(minutes_ago: int) -> None:
        with board.session() as db:
            row = db.execute(select(DisplayDevice)).scalar_one()
            row.last_seen_at = now_sast() - timedelta(minutes=minutes_ago)
            db.commit()

    def watch() -> devices.WatchResult:
        with board.session() as db:
            result = devices.watch_devices(db, alert=sent.append)
            db.commit()
        return result

    last_heard(silent_minutes - 1)
    assert watch().silent == () and sent == []
    last_heard(silent_minutes + 1)
    assert len(watch().silent) == 1
    assert watch().silent == ()  # the same silence, a minute later: no second alert
    assert len(sent) == 1
    assert "\u201cTV by reception\u201d at Zola Clinic" in sent[0]
    assert "has not been heard from since" in sent[0]

    manager = board.world.client("manager.a")
    listed = manager.get(f"/api/v1/sites/{board.world.site_a}/display-devices").json()
    assert listed["items"][0]["status"] == DisplayDeviceStatus.SILENT.value

    assert box.post("/display/heartbeat", json={}).status_code == status.HTTP_200_OK
    back = watch()
    assert len(back.back) == 1 and len(sent) == 2
    assert sent[1].startswith(
        "✅ Waiting-room board back: \u201cTV by reception\u201d at Zola Clinic"
    )


def test_the_operator_sees_every_screen_on_the_platform_and_a_clinic_manager_does_not(
    board: SimpleNamespace,
) -> None:
    """``GET /display-devices`` needs the business-tier grant on ``sites``."""
    _pair(board, _code(_box(board)), label="Reception")
    board.device(site_id=board.world.site_b)
    operator = board.world.client("operator").get("/api/v1/display-devices")
    assert operator.status_code == status.HTTP_200_OK, operator.text
    body = operator.json()
    assert body["total"] == 2 and body["silent"] == 0
    assert sorted(item["site_name"] for item in body["items"]) == [
        "Alex Clinic",
        "Zola Clinic",
    ]
    manager = board.world.client("manager.a").get("/api/v1/display-devices")
    assert manager.status_code == status.HTTP_403_FORBIDDEN


def test_unpaired_boxes_are_tidied_away_after_a_day(board: SimpleNamespace) -> None:
    """A code that nobody typed leaves a row for a day, then the sweep removes it; paired ones stay."""
    _code(_box(board))
    board.device()
    minutes = board.world.settings.display_pairing_code_minutes
    with board.session() as db:
        assert devices.purge_unpaired(db, moment=now_sast() + timedelta(hours=23)) == 0
        later = now_sast() + timedelta(days=1, minutes=minutes + 1)
        assert devices.purge_unpaired(db, moment=later) == 1
        db.commit()
        assert len(db.execute(select(DisplayDevice)).all()) == 1


@pytest.fixture
def webhook() -> Iterator[SimpleNamespace]:
    """A local HTTP server standing in for Slack or Discord, recording what it was sent."""
    received: list[dict[str, object]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{server.server_port}/hook", received=received
    )
    server.shutdown()


def test_an_alert_reaches_the_team_channel_in_each_services_shape_and_a_dead_channel_is_not_an_error(
    board: SimpleNamespace, webhook: SimpleNamespace
) -> None:
    """Slack gets ``text``, Discord ``content``; an unreachable webhook returns False and raises nothing."""
    settings = board.world.settings
    slack = settings.model_copy(update={"team_webhook_url": webhook.url})
    discord = settings.model_copy(
        update={
            "team_webhook_url": webhook.url,
            "team_webhook_kind": TeamWebhookKind.DISCORD,
        }
    )
    assert team_alerts.post_team_alert("📺 silent", slack) is True
    assert team_alerts.post_team_alert("✅ back", discord) is True
    assert webhook.received == [{"text": "📺 silent"}, {"content": "✅ back"}]
    gone = settings.model_copy(
        update={"team_webhook_url": "http://127.0.0.1:9/nothing"}
    )
    assert team_alerts.post_team_alert("lost", gone) is False
    assert team_alerts.post_team_alert("logged only", settings) is False


def test_the_clinic_settings_list_and_the_operator_console_show_the_screens_they_should(
    board: SimpleNamespace,
) -> None:
    """A manager's *Display boards* tab lists the clinic's screens; the operator's console, the silent ones."""
    box = _box(board)
    _pair(board, _code(box), label="TV by reception", queue_ids=[board.world.triage])
    board.device(site_id=board.world.site_b)

    tab = board.world.client("manager.a").get(
        f"/dashboard/sites/{board.world.site_a}/settings/devices"
    )
    assert tab.status_code == status.HTTP_200_OK
    rows = tab.context["device_rows"]  # type: ignore[attr-defined]
    assert [(row.label, row.queue_names, row.status) for row in rows] == [
        ("TV by reception", ("Triage",), DisplayDeviceStatus.ONLINE)
    ]
    # The front desk sees the tab too (read), and the same single screen.
    desk = board.world.client("desk.a").get(
        f"/dashboard/sites/{board.world.site_a}/settings/devices"
    )
    assert len(desk.context["device_rows"]) == 1  # type: ignore[attr-defined]

    with board.session() as db:
        for row in db.execute(select(DisplayDevice)).scalars():
            if row.label == "TV by reception":
                row.last_seen_at = now_sast() - timedelta(minutes=30)
        db.commit()
    operator = board.world.client("operator")
    silent = operator.get("/admin/display-devices/silent")
    assert silent.status_code == status.HTTP_200_OK
    assert [
        (device.label, site.name)
        for device, site in silent.context["rows"]  # type: ignore[attr-defined]
    ] == [("TV by reception", "Zola Clinic")]
    everyone = operator.get("/admin/display-devices/all")
    assert len(everyone.context["rows"]) == 2  # type: ignore[attr-defined]
    assert (
        operator.get("/admin/display-devices/nonsense").status_code
        == status.HTTP_302_FOUND
    )
    refused = board.world.client("manager.a").get("/admin/display-devices/all")
    assert refused.status_code == status.HTTP_403_FORBIDDEN
