"""The clinic manager's settings screens over HTTP (Issue 54).

The screens add no rule: every save is a request to an API route another issue built and tested. What
this file proves, without reading HTML (``docs/IDE/RULES/testing-strategy.mdc``):

* **Scope.** Every tab is the manager's own clinic's: another clinic's id is 404 on every tab.
* **Tabs follow grants.** The manager opens every tab; a receptionist only the waiting-room screen
  (read-only, Issue 27); a nurse none. A tab not offered is refused, not just left out.
* **No developer involved, and every change audited.** Each change a screen makes, sent exactly as
  the screen sends it, lands and writes an audit row naming the manager: the profile and its pin,
  the week, a holiday, a closure and lifting it, a queue's life (add, rename, reorder, deactivate),
  a service's, an invitation and its withdrawal, a role, rooms, switching an account off and on, and
  the display mode.
* **The display preview is the board's rule**, rendered for every mode on the server.
* **The map pin is what discovery reads**, on PostGIS, the moment it is saved.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    DisplayMode,
    QueueKind,
    SaProvince,
    SiteSector,
    SiteStatus,
    UserRole,
)
from src.commons.geo import Coordinates
from src.commons.time import business_date
from src.core import refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import AuditEvent, PublicHoliday, Queue
from src.database.session import get_db
from src.main import create_app
from src.modules.patients.consent import (
    PREVIEW_SAMPLES,
    display_preview,
    project_entry,
)
from src.web.dashboard.settings import SETTINGS_TABS, SettingsSection
from tests.factories import FACTORY_STAFF_PASSWORD, SiteFactory, StaffFactory

MANAGER = "manager.a@clinicq.example"


def _settings(dashboard: SimpleNamespace, section: str = "", site: str = "") -> str:
    """A settings URL at clinic A (or ``site``)."""
    path = f"/dashboard/sites/{site or dashboard.site_a}/settings"
    return f"{path}/{section}" if section else path


def _labels(response) -> list[str]:
    """The tab bar the page was rendered with."""
    assert response.status_code == status.HTTP_200_OK, response.text[:200]
    return [tab.label for tab in response.context["settings_tabs"]]


def _audits(dashboard: SimpleNamespace) -> int:
    """How many audit rows name the manager as the actor."""
    with dashboard.session() as db:
        return db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.actor == MANAGER)
        )


def test_the_manager_opens_every_tab_and_the_bare_url_opens_the_first(
    dashboard: SimpleNamespace,
) -> None:
    """Every tab is its own URL; the settings link and an unknown tab both land on the profile."""
    manager = dashboard.client("manager.a")
    assert manager.get(_settings(dashboard)).headers["location"] == _settings(
        dashboard, "profile"
    )
    assert manager.get(_settings(dashboard, "nonsense")).headers["location"] == (
        _settings(dashboard)
    )
    expected = [
        tab.label for tab in SETTINGS_TABS if tab.section is not SettingsSection.PAYMENT
    ]
    for section in (
        SettingsSection.PROFILE,
        SettingsSection.HOURS,
        SettingsSection.QUEUES,
        SettingsSection.SERVICES,
        SettingsSection.STAFF,
        SettingsSection.DISPLAY,
    ):
        response = manager.get(_settings(dashboard, section.value))
        assert _labels(response) == expected, section
        active = [tab.label for tab in response.context["settings_tabs"] if tab.active]
        assert len(active) == 1


def test_another_clinics_settings_are_not_found_on_every_tab(
    dashboard: SimpleNamespace,
) -> None:
    """Clinic A's manager asking for clinic B's settings, or a made-up clinic's: 404 every time."""
    manager = dashboard.client("manager.a")
    unknown = "0199b0c0-0000-7000-8000-0000000dffff"
    for site in (dashboard.site_b, unknown):
        assert manager.get(_settings(dashboard, site=site)).status_code == (
            status.HTTP_404_NOT_FOUND
        )
        for section in SettingsSection:
            if section is SettingsSection.PAYMENT:
                continue  # served only with the payment filter switched on
            response = manager.get(_settings(dashboard, section.value, site))
            assert response.status_code == status.HTTP_404_NOT_FOUND, (site, section)


def test_tabs_follow_the_grants_at_the_clinic(dashboard: SimpleNamespace) -> None:
    """A receptionist reads only the screen's setting; a nurse opens nothing; neither can guess a URL."""
    desk = dashboard.client("desk.a")
    assert desk.get(_settings(dashboard)).status_code == status.HTTP_403_FORBIDDEN
    for section in ("profile", "hours", "queues", "services", "staff"):
        assert desk.get(_settings(dashboard, section)).status_code == (
            status.HTTP_403_FORBIDDEN
        ), section
    # The screen's two tabs share the display grant a receptionist reads: its setting and its screens.
    assert _labels(desk.get(_settings(dashboard, "display"))) == [
        "Waiting-room screen",
        "Display boards",
    ]

    nurse = dashboard.client("nurse.a")
    for section in ("", "profile", "staff", "display"):
        assert nurse.get(_settings(dashboard, section)).status_code == (
            status.HTTP_403_FORBIDDEN
        ), section


def test_each_change_a_screen_makes_lands_and_is_audited(
    dashboard: SimpleNamespace,
) -> None:
    """How to verify, step 1, and "every settings change is audited": each request as the screen sends it."""
    manager = dashboard.client("manager.a")
    api = f"/api/v1/sites/{dashboard.site_a}"
    holiday = business_date() + timedelta(days=10)
    with dashboard.session() as db:
        db.add(PublicHoliday(holiday_date=holiday, name="A test holiday"))
        desk_id = dashboard.ids["desk.a"]
        db.commit()

    profile = manager.get(_settings(dashboard, "profile")).context["profile"]
    body = {
        "name": "Zola Clinic",
        "slug": profile.slug,
        "sector": SiteSector.PUBLIC.value,
        "location": {"latitude": -26.2485, "longitude": 27.8541},
        "address_line": profile.address_line,
        "suburb": profile.suburb,
        "city": profile.city,
        "province": SaProvince.GAUTENG.value,
        "postal_code": None,
        "phone_e164": None,
        "notes": None,
    }
    queue_body = {
        "name": "Dental",
        "slug": "dental",
        "kind": QueueKind.CONSULTATION.value,
        "room_label": "Room 5",
        "ticket_prefix": "D",
        "display_order": 2,
        "expected_service_minutes": 20,
        "max_daily_capacity": None,
        "recall_timeout_minutes": None,
        "allows_remote_join": True,
        "is_active": True,
    }
    steps: list[tuple[str, str, dict | None]] = [
        ("PUT", api, body),
        (
            "PUT",
            f"{api}/hours",
            {
                "days": [
                    {
                        "weekday": 0,
                        "spans": [
                            {"opens_at": "07:00", "closes_at": "12:00"},
                            {"opens_at": "13:00", "closes_at": "16:00"},
                        ],
                    },
                    {"weekday": 6, "spans": []},
                ]
            },
        ),
        (
            "PUT",
            f"{api}/holidays/{holiday.isoformat()}",
            {"opens_at": "08:00", "closes_at": "11:00"},
        ),
        ("POST", f"{api}/queues", queue_body),
        (
            "POST",
            f"{api}/services",
            {
                "name": "Tooth extraction",
                "slug": "tooth-extraction",
                "category": "other",
                "description": None,
                "expected_minutes": 30,
                "display_order": 0,
                "queue_ids": [dashboard.triage],
                "requires_appointment": False,
                "is_active": True,
            },
        ),
        (
            "POST",
            f"{api}/staff/invitations",
            {
                "email": "new.desk@clinicq.example",
                "role": UserRole.RECEPTIONIST.value,
                "phone": None,
            },
        ),
        ("POST", f"{api}/staff/{desk_id}/roles", {"role": UserRole.NURSE_DOCTOR.value}),
        ("DELETE", f"{api}/staff/{desk_id}/roles/{UserRole.NURSE_DOCTOR.value}", None),
        ("PUT", f"{api}/staff/{desk_id}/queues", {"queue_ids": [dashboard.pharmacy]}),
        ("PUT", f"{api}/staff/{desk_id}/active", {"is_active": False}),
        ("PUT", f"{api}/staff/{desk_id}/active", {"is_active": True}),
        (
            "PUT",
            f"{api}/settings/display",
            {
                "display_mode": DisplayMode.NAME_LITE.value,
                "display_show_comment": False,
                "board_language": "en",
                "announce_audio": True,
                "reason_retention_days": 30,
                "confirm_public_display": True,
                "confirm_comment_with_full_name": False,
            },
        ),
    ]
    for method, url, payload in steps:
        before = _audits(dashboard)
        response = manager.request(method, url, json=payload)
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), (
            method,
            url,
            response.text,
        )
        assert _audits(dashboard) > before, (method, url)

    # The queue's life, from its row on the queues tab: rename, reorder, deactivate.
    queues = manager.get(_settings(dashboard, "queues")).context["queues"]
    dental = next(queue for queue in queues if queue.name == "Dental")
    for method, url, payload in (
        ("PUT", f"{api}/queues/{dental.id}", {**queue_body, "name": "Dental clinic"}),
        (
            "PUT",
            f"{api}/queues",
            {"queue_ids": [dental.id, dashboard.pharmacy, dashboard.triage]},
        ),
        ("DELETE", f"{api}/queues/{dental.id}", None),
    ):
        before = _audits(dashboard)
        assert (
            manager.request(method, url, json=payload).status_code == status.HTTP_200_OK
        )
        assert _audits(dashboard) > before, (method, url)
    # Deactivated, not gone: the queues tab still lists it, and the filter finds it.
    closed = manager.get(_settings(dashboard, "queues"), params={"status": "inactive"})
    assert [queue.name for queue in closed.context["queues"]] == ["Dental clinic"]
    open_only = manager.get(_settings(dashboard, "queues"), params={"status": "active"})
    assert "Dental clinic" not in [queue.name for queue in open_only.context["queues"]]

    # A closure and lifting it, from the hours tab.
    before = _audits(dashboard)
    assert (
        manager.post(f"{api}/closures", json={"reason": "Power failure"}).status_code
        == status.HTTP_201_CREATED
    )
    (closure,) = manager.get(_settings(dashboard, "hours")).context["closures"]
    assert (
        manager.delete(f"{api}/closures/{closure.id}").status_code == status.HTTP_200_OK
    )
    assert _audits(dashboard) >= before + 2
    assert manager.get(_settings(dashboard, "hours")).context["closures"] == []

    # The screens read back what was saved.
    staff = manager.get(_settings(dashboard, "staff")).context
    assert any(
        invitation.email == "new.desk@clinicq.example"
        for invitation, _ in staff["invitations"]
    )
    desk_row = next(row for row in staff["staff_rows"] if row.record["id"] == desk_id)
    assert desk_row.record["queue_ids"] == [dashboard.pharmacy]
    week = manager.get(_settings(dashboard, "hours")).context["week"]
    assert len(week.days[0].spans) == 2

    # A service's life after it was added, and the invitation withdrawn, from their tabs.
    services = manager.get(_settings(dashboard, "services")).context["services"]
    extraction = next(item for item in services if item.slug == "tooth-extraction")
    service_body = {
        key: value
        for key, value in extraction.model_dump(mode="json").items()
        if key
        in {
            "name",
            "slug",
            "category",
            "description",
            "expected_minutes",
            "display_order",
            "requires_appointment",
            "queue_ids",
            "is_active",
        }
    }
    (pending,) = [
        invitation
        for invitation, state in staff["invitations"]
        if invitation.email == "new.desk@clinicq.example" and state == "pending"
    ]
    for method, url, payload in (
        (
            "PUT",
            f"{api}/services/{extraction.id}",
            {**service_body, "expected_minutes": 25},
        ),
        ("DELETE", f"{api}/services/{extraction.id}", None),
        ("DELETE", f"{api}/staff/invitations/{pending.id}", None),
    ):
        before = _audits(dashboard)
        assert (
            manager.request(method, url, json=payload).status_code == status.HTTP_200_OK
        )
        assert _audits(dashboard) > before, (method, url)


def test_removing_the_clinics_last_manager_is_refused_and_says_why(
    dashboard: SimpleNamespace,
) -> None:
    """A destructive change the server will not make: the staff tab shows its sentence."""
    manager = dashboard.client("manager.a")
    refused = manager.delete(
        f"/api/v1/sites/{dashboard.site_a}/staff/{dashboard.ids['manager.a']}/roles/"
        f"{UserRole.CLINIC_MANAGER.value}"
    )
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert "manager" in refused.json()["detail"]


def test_the_display_preview_is_the_boards_own_rule_for_every_mode(
    dashboard: SimpleNamespace,
) -> None:
    """What the preview shows under each mode is exactly what the projection returns."""
    page = dashboard.client("manager.a").get(_settings(dashboard, "display"))
    preview = page.context["preview"]
    for key, show_comment in (("with_reason", True), ("without_reason", False)):
        assert set(preview[key]) == {mode.value for mode in DisplayMode}
        for mode in DisplayMode:
            expected = [
                project_entry(
                    ticket_number=sample.ticket_number,
                    display_mode=mode,
                    display_name=sample.display_name,
                    comment=sample.comment if show_comment else None,
                    name_consented=sample.name_consented,
                    comment_consented=sample.comment_consented,
                )
                for sample in PREVIEW_SAMPLES
            ]
            assert preview[key][mode.value] == expected
    rows = display_preview(show_comment=True)
    assert all(row.name is None for row in rows[DisplayMode.NUMBER_ONLY.value])
    lite = [row.name for row in rows[DisplayMode.NAME_LITE.value]]
    assert lite == ["Thandiwe M.", "Sipho D.", None, None]
    full = [(row.name, row.comment) for row in rows[DisplayMode.FULL.value]]
    assert full == [
        ("Thandiwe Mokoena", "Chest pain"),
        ("Sipho Dlamini", None),
        (None, None),
        (None, None),
    ]
    assert all(
        row.comment is None
        for rows_by_mode in display_preview(show_comment=False).values()
        for row in rows_by_mode
    )


# --------------------------------------------------------------------------------------
# The map pin, on PostGIS: discovery reads the saved point at once
# --------------------------------------------------------------------------------------


@pytest.fixture
def postgis_clinic(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    """One verified clinic with a manager, in a migrated PostGIS database, and the real app."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="settings-map-test-secret-min-32-characters",
        auth_password_login_enabled=True,
        smtp_host="",
    )
    factory = sessionmaker(bind=migrated_engine, autoflush=False)
    with factory() as db:
        sync_rbac_catalog(db)
        site = SiteFactory.create(
            db,
            status=SiteStatus.VERIFIED,
            location=Coordinates(latitude=-26.1929, longitude=28.0305),
        )
        db.add(
            Queue(site_id=site.id, name="General", slug="general", ticket_prefix="G")
        )
        StaffFactory.create(
            db, email=MANAGER, role=UserRole.CLINIC_MANAGER, site_id=site.id
        )
        db.commit()
        site_id, slug = site.id, site.slug

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    for module in (security, refresh_token_policy):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    signed_in = client.post(
        "/api/v1/auth/password/login",
        json={"email": MANAGER, "password": FACTORY_STAFF_PASSWORD},
    )
    assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
    client.headers["X-CSRF-Token"] = client.cookies.get(settings.csrf_cookie_name) or ""
    yield SimpleNamespace(client=client, site_id=site_id, slug=slug)
    app.dependency_overrides.clear()


@pytest.mark.postgres
def test_moving_the_pin_moves_the_clinic_in_discovery_at_once(
    postgis_clinic: SimpleNamespace,
) -> None:
    """How to verify, step 2: save a new point from the profile tab, then search near it."""
    manager = postgis_clinic.client
    soweto = {"lat": -26.2485, "lon": 27.8541}
    near = {**soweto, "radius_m": 2000}
    before = manager.get("/api/v1/clinics/nearby", params=near).json()
    assert postgis_clinic.slug not in [item["slug"] for item in before["items"]]

    current = manager.get(f"/api/v1/sites/{postgis_clinic.site_id}").json()
    body = {
        key: current[key]
        for key in (
            "name",
            "slug",
            "sector",
            "address_line",
            "suburb",
            "city",
            "province",
            "postal_code",
            "phone_e164",
            "notes",
        )
    }
    body["location"] = {"latitude": soweto["lat"], "longitude": soweto["lon"]}
    saved = manager.put(f"/api/v1/sites/{postgis_clinic.site_id}", json=body)
    assert saved.status_code == status.HTTP_200_OK, saved.text

    after = manager.get("/api/v1/clinics/nearby", params=near).json()
    found = next(item for item in after["items"] if item["slug"] == postgis_clinic.slug)
    assert found["distance_m"] < 50
