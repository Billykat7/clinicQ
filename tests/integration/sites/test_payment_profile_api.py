"""A private clinic declares what it accepts; a public clinic cannot (Issue 37), over HTTP.

The clinic-side half of the payment and medical-aid directory tag, on the M4 fixture's two clinics:

* **only a private clinic can hold a payment profile, enforced on the server**: a public clinic's
  save is a ``409`` whatever the client sends, and a clinic that turns public loses its profile;
* scheme tags come from the **controlled list, with a free-text other**;
* saving confirms; re-confirming clears the six-month **staleness**;
* only the clinic manager changes it, another clinic's is a ``404``, and every change is audited;
* the notice patients see is served with it.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from starlette import status

from src.commons.enums import AuditEntityType, MedicalAidScheme, SiteSector
from src.commons.time import now_sast
from src.database.models import AuditEvent, Site, SitePaymentProfile
from src.modules.sites.payment_profile import (
    CLINIC_REPORTED_NOTICE,
    PUBLIC_CLINIC_REFUSAL,
)

_DECLARED = {
    "accepts_cash": True,
    "accepts_card": True,
    "schemes": [MedicalAidScheme.DISCOVERY_HEALTH.value, MedicalAidScheme.OTHER.value],
    "other_scheme_name": "Umvuzo Health",
    "copay_notice": "A co-payment may apply for some plans.",
}


def _path(clinics: SimpleNamespace, site: str | None = None) -> str:
    """A clinic's payment profile route."""
    return f"/api/v1/sites/{site or clinics.site_a}/payment-profile"


def _set_sector(
    clinics: SimpleNamespace, sector: SiteSector, site: str | None = None
) -> None:
    """Set a fixture clinic's sector. The factory cycles through the demo clinics, so which sector
    a fixture clinic starts with depends on how many clinics other tests built first: never rely on
    it."""
    with clinics.session() as db:
        db.execute(
            update(Site)
            .where(Site.id == (site or clinics.site_a))
            .values(sector=sector.value)
        )
        db.commit()


def _make_private(clinics: SimpleNamespace, site: str | None = None) -> None:
    """Turn a fixture clinic into a private practice."""
    _set_sector(clinics, SiteSector.PRIVATE, site)


def test_a_public_clinic_cannot_hold_a_payment_profile_whatever_the_client_sends(
    clinics: SimpleNamespace,
) -> None:
    """The server refuses the save with 409 and stores nothing; the read says it does not apply."""
    _set_sector(clinics, SiteSector.PUBLIC)
    manager = clinics.client("manager.a@clinicq.example")
    refused = manager.put(_path(clinics), json=_DECLARED)
    read = manager.get(_path(clinics))

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["detail"] == PUBLIC_CLINIC_REFUSAL
    assert read.status_code == status.HTTP_200_OK
    assert read.json()["applicable"] is False and read.json()["schemes"] == []
    assert (
        manager.post(f"{_path(clinics)}/confirm").status_code
        == status.HTTP_409_CONFLICT
    )
    with clinics.session() as db:
        assert db.get(SitePaymentProfile, clinics.site_a) is None


def test_a_private_clinic_declares_schemes_from_the_list_with_a_named_other(
    clinics: SimpleNamespace,
) -> None:
    """Saved, read back with labels and the notice, confirmed today and not stale."""
    _make_private(clinics)
    manager = clinics.client("manager.a@clinicq.example")
    saved = manager.put(_path(clinics), json=_DECLARED)

    assert saved.status_code == status.HTTP_200_OK, saved.text
    body = manager.get(_path(clinics)).json()
    assert body["applicable"] is True
    assert (body["accepts_cash"], body["accepts_card"]) == (True, True)
    assert [s["label"] for s in body["schemes"]] == [
        "Discovery Health",
        "Umvuzo Health",
    ]
    assert body["notice"] == CLINIC_REPORTED_NOTICE
    assert body["stale"] is False and body["last_confirmed_at"] is not None
    assert {o["value"] for o in body["scheme_options"]} == {
        s.value for s in MedicalAidScheme
    }

    without_name = manager.put(
        _path(clinics), json={**_DECLARED, "other_scheme_name": None}
    )
    assert without_name.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_a_profile_not_confirmed_in_six_months_is_stale_until_reconfirmed(
    clinics: SimpleNamespace,
) -> None:
    """Seven months ago: stale. "Still correct": today, and not stale."""
    _make_private(clinics)
    manager = clinics.client("manager.a@clinicq.example")
    assert (
        manager.post(f"{_path(clinics)}/confirm").status_code
        == status.HTTP_404_NOT_FOUND
    )
    manager.put(_path(clinics), json=_DECLARED)
    with clinics.session() as db:
        db.execute(
            update(SitePaymentProfile)
            .where(SitePaymentProfile.site_id == clinics.site_a)
            .values(last_confirmed_at=now_sast() - timedelta(days=213))
        )
        db.commit()

    assert manager.get(_path(clinics)).json()["stale"] is True
    confirmed = manager.post(f"{_path(clinics)}/confirm")
    assert confirmed.status_code == status.HTTP_200_OK
    assert confirmed.json()["stale"] is False


def test_a_clinic_that_becomes_public_loses_its_profile(
    clinics: SimpleNamespace,
) -> None:
    """The rule follows the sector: changing it to public removes what the clinic had declared."""
    _make_private(clinics)
    operator_changes = clinics.client("manager.a@clinicq.example")
    operator_changes.put(_path(clinics), json=_DECLARED)
    site = operator_changes.get(f"/api/v1/sites/{clinics.site_a}").json()
    profile_fields = (
        "name",
        "slug",
        "location",
        "address_line",
        "suburb",
        "city",
        "province",
        "postal_code",
        "phone_e164",
        "notes",
    )
    body = {field: site[field] for field in profile_fields} | {
        "sector": SiteSector.PUBLIC.value
    }
    updated = operator_changes.put(f"/api/v1/sites/{clinics.site_a}", json=body)

    assert updated.status_code == status.HTTP_200_OK, updated.text
    with clinics.session() as db:
        assert db.get(SitePaymentProfile, clinics.site_a) is None
        contexts = (
            db.execute(
                select(AuditEvent.context).where(
                    AuditEvent.entity_type == AuditEntityType.SITE.value
                )
            )
            .scalars()
            .all()
        )
    assert any("removed its payment profile" in (c or "") for c in contexts)


def test_only_the_manager_changes_it_and_another_clinic_is_a_404(
    clinics: SimpleNamespace,
) -> None:
    """The front desk reads it and is refused the change; Clinic B's manager cannot see Clinic A's."""
    _make_private(clinics)
    desk = clinics.client("desk.a@clinicq.example")
    assert desk.get(_path(clinics)).status_code == status.HTTP_200_OK
    assert (
        desk.put(_path(clinics), json=_DECLARED).status_code
        == status.HTTP_403_FORBIDDEN
    )
    stranger = clinics.client("manager.b@clinicq.example")
    assert stranger.get(_path(clinics)).status_code == status.HTTP_404_NOT_FOUND
    assert (
        stranger.put(_path(clinics), json=_DECLARED).status_code
        == status.HTTP_404_NOT_FOUND
    )

    clinics.client("manager.a@clinicq.example").put(_path(clinics), json=_DECLARED)
    with clinics.session() as db:
        contexts = (
            db.execute(
                select(AuditEvent.context).where(
                    AuditEvent.entity_type == AuditEntityType.SITE.value
                )
            )
            .scalars()
            .all()
        )
    assert any("declared the payment profile" in (c or "") for c in contexts)


def test_the_editor_page_is_served_only_with_the_feature_switched_on(
    clinics: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PAYMENT_FILTER_ENABLED`` off: the page is a 404. On: the manager gets it."""
    from src.web import routes

    page = f"/dashboard/sites/{clinics.site_a}/settings/payment"
    manager = clinics.client("manager.a@clinicq.example")
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: clinics.settings.model_copy(update={"payment_filter_enabled": False}),
    )
    assert manager.get(page).status_code == status.HTTP_404_NOT_FOUND
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: clinics.settings.model_copy(update={"payment_filter_enabled": True}),
    )
    assert manager.get(page).status_code == status.HTTP_200_OK
