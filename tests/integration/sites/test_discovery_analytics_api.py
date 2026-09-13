"""A clinic's discovery analytics switch and view-to-join report, over HTTP (Issue 38).

* the switch and the report are the clinic manager's: a receptionist is refused both;
* turning analytics off is audited, and stops new views of the clinic being counted;
* the report counts the clinic's own events only, over a bounded range of service days.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select
from starlette import status

from src.commons.enums import AuditAction, AuditEntityType, DiscoveryChannel
from src.commons.time import now_sast
from src.database.models import AuditEvent, DiscoveryEvent
from src.modules.discovery import analytics


def _switch(clinics: SimpleNamespace, site: str | None = None) -> str:
    return f"/api/v1/sites/{site or clinics.site_a}/settings/analytics"


def _report(clinics: SimpleNamespace, site: str | None = None) -> str:
    return f"/api/v1/sites/{site or clinics.site_a}/reports/discovery-conversion"


def _view(clinics: SimpleNamespace, site: str, days_ago: int = 0) -> None:
    with clinics.session() as db:
        analytics.record_clinic_viewed(
            db,
            site,
            channel=DiscoveryChannel.WEB,
            session_token=analytics.new_session_token(),
            settings=clinics.settings,
            moment=now_sast() - timedelta(days=days_ago),
        )


def test_a_new_clinic_is_counted_and_says_what_that_means(
    clinics: SimpleNamespace,
) -> None:
    body = clinics.client("manager.a@clinicq.example").get(_switch(clinics)).json()
    assert body["analytics_enabled"] is True
    assert body["explanation"] == analytics.ANALYTICS_EXPLANATION


def test_opting_out_is_audited_and_stops_new_views_being_counted(
    clinics: SimpleNamespace,
) -> None:
    manager = clinics.client("manager.a@clinicq.example")
    _view(clinics, clinics.site_a)

    off = manager.put(_switch(clinics), json={"analytics_enabled": False})
    _view(clinics, clinics.site_a)

    assert off.status_code == status.HTTP_200_OK, off.text
    assert off.json()["analytics_enabled"] is False
    with clinics.session() as db:
        assert len(db.execute(select(DiscoveryEvent)).scalars().all()) == 1
        audit = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.SITE.value,
                AuditEvent.action == AuditAction.UPDATE.value,
            )
        ).scalar_one()
    assert "discovery analytics off" in (audit.context or "")
    report = manager.get(_report(clinics)).json()
    assert report["views"] == 1
    assert report["analytics_enabled"] is False


def test_an_unchanged_save_writes_no_audit_row(clinics: SimpleNamespace) -> None:
    clinics.client("manager.a@clinicq.example").put(
        _switch(clinics), json={"analytics_enabled": True}
    )
    with clinics.session() as db:
        assert db.execute(select(AuditEvent)).scalars().all() == []


def test_the_front_desk_sees_neither_the_switch_nor_the_report(
    clinics: SimpleNamespace,
) -> None:
    desk = clinics.client("desk.a@clinicq.example")
    assert desk.get(_switch(clinics)).status_code == status.HTTP_403_FORBIDDEN
    assert (
        desk.put(_switch(clinics), json={"analytics_enabled": False}).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert desk.get(_report(clinics)).status_code == status.HTTP_403_FORBIDDEN


def test_the_report_counts_this_clinics_views_in_the_range_only(
    clinics: SimpleNamespace,
) -> None:
    for _ in range(3):
        _view(clinics, clinics.site_a)
    _view(clinics, clinics.site_a, days_ago=45)
    _view(clinics, clinics.site_b)

    body = clinics.client("manager.a@clinicq.example").get(_report(clinics)).json()

    assert body["views"] == 3
    assert body["joins_completed"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["end"] and body["start"], (
        "the default range is the last 30 days, and the answer says which"
    )
    wider = clinics.client("manager.a@clinicq.example").get(
        _report(clinics),
        params={"start": (now_sast().date() - timedelta(days=60)).isoformat()},
    )
    assert wider.json()["views"] == 4


def test_a_backwards_or_overlong_range_is_refused(clinics: SimpleNamespace) -> None:
    manager = clinics.client("manager.a@clinicq.example")
    today = now_sast().date()
    backwards = manager.get(
        _report(clinics),
        params={
            "start": today.isoformat(),
            "end": (today - timedelta(days=1)).isoformat(),
        },
    )
    too_long = manager.get(
        _report(clinics),
        params={
            "start": (today - timedelta(days=400)).isoformat(),
            "end": today.isoformat(),
        },
    )
    assert backwards.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert too_long.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_another_clinics_report_is_not_found(clinics: SimpleNamespace) -> None:
    manager = clinics.client("manager.a@clinicq.example")
    assert manager.get(_report(clinics, clinics.site_b)).status_code == 404
    assert manager.get(_switch(clinics, clinics.site_b)).status_code == 404
