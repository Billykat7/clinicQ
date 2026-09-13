"""The payment and medical-aid filter in discovery, against PostGIS (Issue 37).

The patient-facing half: the filter exists **only under Private**, matches what clinics **report**,
carries the clinic-reported notice **wherever the data appears**, marks a profile not confirmed in six
months as **stale**, and is **behind a flag** so it can ship later without a code change.

Two private demo clinics get profiles: Medicross Meldene (cash, card, Discovery Health and Bonitas,
confirmed today) and Medicross Randburg (card only, GEMS, confirmed seven months ago).
"""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import MedicalAidScheme, SectorFilter
from src.commons.ids import new_id
from src.commons.time import now_sast
from src.core.config import Settings, get_settings
from src.database.models import SitePaymentMedicalAid, SitePaymentProfile
from src.modules.discovery.profile import clinic_profile
from src.modules.discovery.service import (
    PAYMENT_FILTER_OFF,
    PAYMENT_FILTER_PRIVATE_ONLY,
    PaymentFilter,
    PaymentFilterRefusedError,
    find_nearby_sites,
)
from src.modules.sites.payment_profile import CLINIC_REPORTED_NOTICE
from src.web.discover import (
    PAYMENT_FILTER_NOTICE,
    DiscoverPage,
    Origin,
    detail_view,
    discover_page,
    page_href,
)
from tests.integration.discovery.conftest import JOHANNESBURG

pytestmark = pytest.mark.postgres

_NEARBY = "/api/v1/clinics/nearby"
_WIDE = 20_000


def _declare(
    db: Session,
    site_id: str,
    *,
    cash: bool,
    card: bool,
    schemes: list[MedicalAidScheme],
    days_ago: int = 0,
) -> None:
    """Store a profile directly, as the clinic's editor would have."""
    db.add(
        SitePaymentProfile(
            site_id=site_id,
            accepts_cash=cash,
            accepts_card=card,
            last_confirmed_at=now_sast() - timedelta(days=days_ago),
        )
    )
    db.flush()
    for scheme in schemes:
        db.add(SitePaymentMedicalAid(id=new_id(), site_id=site_id, scheme=scheme.value))


@pytest.fixture
def payments(directory: SimpleNamespace) -> SimpleNamespace:
    """The directory with two private profiles and an API that has the feature switched on."""
    with directory.session() as db:
        _declare(
            db,
            directory.ids["medicross-meldene"],
            cash=True,
            card=True,
            schemes=[MedicalAidScheme.DISCOVERY_HEALTH, MedicalAidScheme.BONITAS],
        )
        _declare(
            db,
            directory.ids["medicross-randburg"],
            cash=False,
            card=True,
            schemes=[MedicalAidScheme.GEMS],
            days_ago=213,
        )
        db.commit()
    on = Settings(
        _env_file=None,  # type: ignore[call-arg]
        jwt_secret="m5-discovery-test-secret-min-32-characters",
        payment_filter_enabled=True,
    )
    directory.app.dependency_overrides[get_settings] = lambda: on
    return directory


def _page(directory: SimpleNamespace, **kwargs: object) -> DiscoverPage:
    """The discovery page near Johannesburg, the feature on unless the test says otherwise."""
    with directory.session() as db:
        return discover_page(
            db,
            lat=JOHANNESBURG.latitude,
            lon=JOHANNESBURG.longitude,
            radius_m=_WIDE,
            **{"payments_enabled": True, **kwargs},  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------------------
# Completely absent outside Private
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("sector", [SectorFilter.PUBLIC, SectorFilter.ALL])
def test_the_payment_filter_is_absent_unless_private_is_selected(
    payments: SimpleNamespace, sector: SectorFilter
) -> None:
    """Under Public or All the page has no filter at all, and a leftover choice is dropped."""
    page = _page(payments, sector=sector, payment=PaymentFilter(accepts_cash=True))
    assert page.payment_filter is None
    assert page.results is not None and page.results.payment is None
    assert "accepts_cash" not in (urlsplit(page.results.href()).query)


def test_the_api_refuses_a_payment_filter_outside_private(
    payments: SimpleNamespace,
) -> None:
    """The server holds the rule too: a payment filter under Public or All is a 422 that says why."""
    for sector in ("public", "all"):
        refused = payments.client.get(
            _NEARBY,
            params={
                "lat": JOHANNESBURG.latitude,
                "lon": JOHANNESBURG.longitude,
                "sector": sector,
                "medical_aid": "bonitas",
            },
        )
        assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert refused.json()["detail"] == PAYMENT_FILTER_PRIVATE_ONLY
    with payments.session() as db, pytest.raises(PaymentFilterRefusedError):
        find_nearby_sites(
            db,
            JOHANNESBURG,
            sector=SectorFilter.PUBLIC,
            payment=PaymentFilter(accepts_card=True),
            payments_enabled=True,
        )


# --------------------------------------------------------------------------------------
# Under Private: filtering on what clinics report
# --------------------------------------------------------------------------------------


def test_under_private_the_filter_is_offered_and_narrows_by_what_clinics_report(
    payments: SimpleNamespace,
) -> None:
    """Bonitas finds Meldene only; card finds both; cash and GEMS together find neither."""
    page = _page(payments, sector=SectorFilter.PRIVATE)
    assert page.payment_filter is not None
    assert page.payment_filter.notice == PAYMENT_FILTER_NOTICE
    assert "gems" in {c.value for c in page.payment_filter.schemes}

    def slugs(payment: PaymentFilter) -> list[str]:
        with payments.session() as db:
            result = find_nearby_sites(
                db,
                JOHANNESBURG,
                radius_m=_WIDE,
                sector=SectorFilter.PRIVATE,
                payment=payment,
                payments_enabled=True,
            )
        return [clinic.slug for clinic in result.clinics]

    assert slugs(PaymentFilter(schemes=frozenset({MedicalAidScheme.BONITAS}))) == [
        "medicross-meldene"
    ]
    assert slugs(PaymentFilter(accepts_card=True)) == [
        "medicross-meldene",
        "medicross-randburg",
    ]
    assert slugs(
        PaymentFilter(
            schemes=frozenset({MedicalAidScheme.GEMS, MedicalAidScheme.BONITAS})
        )
    ) == ["medicross-meldene", "medicross-randburg"]
    assert (
        slugs(
            PaymentFilter(accepts_cash=True, schemes=frozenset({MedicalAidScheme.GEMS}))
        )
        == []
    )

    chosen = _page(
        payments,
        sector=SectorFilter.PRIVATE,
        payment=PaymentFilter(schemes=frozenset({MedicalAidScheme.BONITAS})),
    )
    assert chosen.results is not None
    assert parse_qs(urlsplit(chosen.results.href()).query)["medical_aid"] == ["bonitas"]
    assert [c.value for c in chosen.payment_filter.schemes if c.selected] == ["bonitas"]  # type: ignore[union-attr]


# --------------------------------------------------------------------------------------
# The notice wherever the data appears, and staleness
# --------------------------------------------------------------------------------------


def test_payment_data_carries_the_clinic_reported_notice_everywhere_it_appears(
    payments: SimpleNamespace,
) -> None:
    """The list card, the detail page, the nearby API and the profile API all say it."""
    page = _page(payments, sector=SectorFilter.PRIVATE)
    assert page.results is not None
    cards = {card.slug: card for card in page.results.cards}
    assert cards["medicross-meldene"].payment is not None
    assert cards["medicross-meldene"].payment.notice == CLINIC_REPORTED_NOTICE

    with payments.session() as db:
        profile = clinic_profile(db, "medicross-meldene", payments_enabled=True)
    assert profile is not None
    detail = detail_view(profile, join_enabled=False)
    assert (
        detail.payment is not None and detail.payment.notice == CLINIC_REPORTED_NOTICE
    )
    assert detail.payment.medical_aids == "Medical aids: Discovery Health, Bonitas"

    nearby = payments.client.get(
        _NEARBY,
        params={
            "lat": JOHANNESBURG.latitude,
            "lon": JOHANNESBURG.longitude,
            "radius_m": _WIDE,
            "sector": "private",
        },
    ).json()
    reported = [item["payment"] for item in nearby["items"] if item["payment"]]
    assert reported and all(p["notice"] == CLINIC_REPORTED_NOTICE for p in reported)
    api_profile = payments.client.get("/api/v1/clinics/medicross-meldene").json()
    assert api_profile["payment"]["notice"] == CLINIC_REPORTED_NOTICE
    # A public clinic never carries payment information.
    public = payments.client.get("/api/v1/clinics/hillbrow-chc").json()
    assert public["payment"] is None


def test_a_profile_not_confirmed_in_six_months_is_marked_stale(
    payments: SimpleNamespace,
) -> None:
    """Randburg was confirmed 213 days ago: stale on the card and in the API; Meldene is not."""
    page = _page(payments, sector=SectorFilter.PRIVATE)
    assert page.results is not None
    cards = {card.slug: card for card in page.results.cards}
    stale = cards["medicross-randburg"].payment
    assert stale is not None and stale.stale_label is not None
    assert stale.stale_label.startswith("Not confirmed by the clinic since ")
    assert cards["medicross-meldene"].payment.stale_label is None  # type: ignore[union-attr]

    nearby = payments.client.get(
        _NEARBY,
        params={
            "lat": JOHANNESBURG.latitude,
            "lon": JOHANNESBURG.longitude,
            "radius_m": _WIDE,
            "sector": "private",
        },
    ).json()
    flags = {
        item["slug"]: item["payment"]["stale"]
        for item in nearby["items"]
        if item["payment"]
    }
    assert flags == {"medicross-meldene": False, "medicross-randburg": True}


# --------------------------------------------------------------------------------------
# Behind a flag
# --------------------------------------------------------------------------------------


def test_with_the_flag_off_nothing_patient_facing_shows_or_filters_by_payment(
    payments: SimpleNamespace,
) -> None:
    """No filter under Private, no payment on any result or profile, and the API refuses a filter."""
    page = _page(payments, sector=SectorFilter.PRIVATE, payments_enabled=False)
    assert page.payment_filter is None
    assert page.results is not None and all(
        card.payment is None for card in page.results.cards
    )
    with payments.session() as db:
        profile = clinic_profile(db, "medicross-meldene", payments_enabled=False)
    assert profile is not None and profile.payment is None
    assert detail_view(profile, join_enabled=False).payment_note is None

    off = Settings(
        _env_file=None,  # type: ignore[call-arg]
        jwt_secret="m5-discovery-test-secret-min-32-characters",
    )
    assert off.payment_filter_enabled is False
    payments.app.dependency_overrides[get_settings] = lambda: off
    refused = payments.client.get(
        _NEARBY,
        params={
            "lat": JOHANNESBURG.latitude,
            "lon": JOHANNESBURG.longitude,
            "sector": "private",
            "accepts_cash": "true",
        },
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert refused.json()["detail"] == PAYMENT_FILTER_OFF
    plain = payments.client.get(
        _NEARBY,
        params={
            "lat": JOHANNESBURG.latitude,
            "lon": JOHANNESBURG.longitude,
            "radius_m": _WIDE,
            "sector": "private",
        },
    ).json()
    assert all(item["payment"] is None for item in plain["items"])


def test_the_address_carries_payment_choices_only_under_private() -> None:
    """Moving the toggle away from Private leaves the choices behind in the URL too."""
    origin = Origin(latitude=-26.205, longitude=28.04)
    payment = PaymentFilter(
        accepts_cash=True, schemes=frozenset({MedicalAidScheme.GEMS})
    )
    from src.commons.enums import DiscoverySort

    private = page_href(
        origin, SectorFilter.PRIVATE, DiscoverySort.NEAREST, 10_000, payment=payment
    )
    public = page_href(
        origin, SectorFilter.PUBLIC, DiscoverySort.NEAREST, 10_000, payment=payment
    )
    assert parse_qs(urlsplit(private).query) == {
        "lat": ["-26.205"],
        "lon": ["28.04"],
        "sector": ["private"],
        "accepts_cash": ["true"],
        "medical_aid": ["gems"],
    }
    assert "accepts_cash" not in public and "medical_aid" not in public
