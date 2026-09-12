"""Geocoding happens on the server, is off by default, and never trusts what comes back (Issue 23).

Unit tests with a stubbed transport: what is under test is this module's own rules (off unless
configured, a User-Agent required, results outside the operating country dropped, a provider
failure turned into a clear refusal), not somebody else's HTTP API.
"""

from __future__ import annotations

import httpx
import pytest

from src.commons.enums import AppEnvironment, GeocodingProvider
from src.core.config import Settings
from src.modules.sites.geocoding import (
    GeocodingUnavailableError,
    geocode_address,
)

_HILLBROW = {
    "lat": "-26.19355",
    "lon": "28.04540",
    "display_name": "Hillbrow Community Health Centre, Johannesburg",
}
_LONDON = {"lat": "51.5072", "lon": "-0.1276", "display_name": "London"}


def _settings(**overrides: object) -> Settings:
    """Settings with a provider configured, isolated from the developer's ``.env``."""
    values: dict[str, object] = {
        "environment": AppEnvironment.DEVELOPMENT,
        "geocoding_provider": GeocodingProvider.NOMINATIM,
        "geocoding_user_agent": "ClinicQ-test/0.4 (tests@clinicq.example)",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _client(payload: object, status_code: int = 200) -> httpx.Client:
    """An ``httpx.Client`` whose transport answers every request with ``payload``."""
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=payload)
        )
    )


def test_no_provider_configured_refuses_rather_than_calling_somebody_elses_service() -> (
    None
):
    """The default. An unconfigured deployment asks the operator for the coordinate."""
    with pytest.raises(GeocodingUnavailableError, match="GEOCODING_PROVIDER"):
        geocode_address(
            "Hillbrow CHC",
            settings=_settings(geocoding_provider=GeocodingProvider.NONE),
        )


def test_nominatim_is_not_called_without_a_user_agent_naming_this_deployment() -> None:
    """Its usage policy requires one, so an empty setting is unconfigured, not anonymous."""
    with pytest.raises(GeocodingUnavailableError, match="GEOCODING_USER_AGENT"):
        geocode_address(
            "Hillbrow CHC",
            settings=_settings(geocoding_user_agent="  "),
            client=_client([_HILLBROW]),
        )


def test_a_candidate_inside_the_operating_area_comes_back_with_its_label() -> None:
    """The happy path: latitude and longitude read in the right order, with the provider's label."""
    with _client([_HILLBROW]) as client:
        places = geocode_address("Hillbrow CHC", settings=_settings(), client=client)
    assert len(places) == 1
    assert places[0].point.latitude == pytest.approx(-26.19355)
    assert places[0].point.longitude == pytest.approx(28.04540)
    assert "Hillbrow" in places[0].label


def test_a_match_in_another_country_is_dropped_rather_than_offered() -> None:
    """A geocoder will happily answer with London; that is never the clinic being registered."""
    with _client([_LONDON, _HILLBROW]) as client:
        places = geocode_address("Clinic", settings=_settings(), client=client)
    assert [round(place.point.latitude, 3) for place in places] == [-26.194]


def test_a_malformed_row_is_skipped_and_does_not_fail_the_whole_lookup() -> None:
    """One unusable candidate must not lose the usable ones beside it."""
    with _client([{"display_name": "no coordinates"}, _HILLBROW]) as client:
        places = geocode_address("Clinic", settings=_settings(), client=client)
    assert len(places) == 1


def test_a_provider_failure_becomes_a_refusal_the_operator_can_act_on() -> None:
    """A 500 from the provider is not a 500 from ClinicQ: it says to type the coordinate in."""
    with (
        _client({"error": "upstream"}, status_code=500) as client,
        pytest.raises(GeocodingUnavailableError, match="manually"),
    ):
        geocode_address("Clinic", settings=_settings(), client=client)


def test_an_empty_address_asks_nobody_anything() -> None:
    """Whitespace is not a lookup; no request is made and no error is raised."""

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the provider was called for an empty address")

    with httpx.Client(transport=httpx.MockTransport(_refuse)) as client:
        assert geocode_address("   ", settings=_settings(), client=client) == []
