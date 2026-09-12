"""Turning a typed address into a coordinate — on the server, never in the browser (Issue 23).

**Why this module exists at all.** The obvious implementation is a few lines of JavaScript on the
clinic-profile form calling a geocoder directly. This application's Content-Security-Policy allows
``connect-src 'self'`` and nothing else (``src.core.security_headers``), so that request would be
blocked by the browser with no visible error — and even if it were allowed, it would put a clinic's
address and the deployment's API key in front of a third party from every operator's laptop. So the
browser posts the address to ClinicQ, and ClinicQ asks the provider: one egress point, one place to
put the timeout, the rate limit and the audit line.

**It is off unless a deployment turns it on.** :data:`~src.commons.enums.GeocodingProvider.NONE` is
the default, and then :func:`geocode_address` says so rather than quietly reaching out to somebody
else's service. An operator types the coordinate in; site creation never depends on a lookup
succeeding.

**A result is a suggestion, not an answer.** Whatever comes back is validated against the operating
country's bounding box like any hand-typed pair (:mod:`src.commons.geo`), and the caller is
expected to show it on a map and let a human accept or override it.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass

import httpx

from src.commons.enums import GeocodingProvider
from src.commons.geo import (
    OPERATING_COUNTRY,
    CoordinateOutOfRangeError,
    Coordinates,
    assert_within_operating_area,
)
from src.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: How many candidates to ask the provider for. More than one, because the first hit for a clinic
#: name is often the suburb; the caller shows the list and a person picks.
MAX_RESULTS = 5


class GeocodingUnavailableError(RuntimeError):
    """No provider is configured, or the configured one could not be reached."""


@dataclass(frozen=True, slots=True)
class GeocodedPlace:
    """One candidate position for a typed address, with the provider's own label for it."""

    #: The address as the provider spells it, for a person to recognise.
    label: str
    point: Coordinates


def _nominatim_results(
    settings: Settings, address: str, *, client: httpx.Client | None = None
) -> list[dict[str, object]]:
    """Ask a Nominatim instance for ``address``; return its raw JSON array.

    Nominatim's usage policy requires a descriptive ``User-Agent`` identifying the application, so
    the setting has no usable default and a deployment that leaves it empty is treated as
    unconfigured rather than sending an anonymous request.
    """
    if not settings.geocoding_user_agent.strip():
        raise GeocodingUnavailableError(
            "GEOCODING_USER_AGENT must name this deployment before Nominatim may be called."
        )
    url = f"{settings.geocoding_base_url.rstrip('/')}/search"
    params: dict[str, str | int] = {
        "q": address,
        "format": "jsonv2",
        "limit": MAX_RESULTS,
        "countrycodes": settings.geocoding_country_code,
    }
    headers = {"User-Agent": settings.geocoding_user_agent}
    try:
        with ExitStack() as stack:
            session = client or stack.enter_context(
                httpx.Client(timeout=settings.geocoding_timeout_seconds)
            )
            response = session.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # The address is not logged: it is the clinic's, and the log ships to S3.
        logger.warning("Geocoding lookup failed: %s", type(exc).__name__)
        raise GeocodingUnavailableError(
            "The geocoding service did not answer. Enter the coordinate manually."
        ) from exc
    return payload if isinstance(payload, list) else []


def geocode_address(
    address: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[GeocodedPlace]:
    """Return candidate positions for ``address``, inside the operating country, best first.

    Args:
        address: The address as typed by the operator.
        settings: Injected in tests; the process settings otherwise.
        client: An ``httpx.Client`` to use instead of a one-shot request. Tests pass a transport
            here; production leaves it ``None``.

    Returns:
        Between zero and :data:`MAX_RESULTS` candidates. Anything the provider returns outside
        the operating country is dropped rather than offered, because a match in another country
        is never the clinic being registered.

    Raises:
        GeocodingUnavailableError: If no provider is configured, or the provider did not answer.
    """
    settings = settings or get_settings()
    if settings.geocoding_provider is GeocodingProvider.NONE:
        raise GeocodingUnavailableError(
            "No geocoding provider is configured (GEOCODING_PROVIDER). "
            "Enter the clinic's coordinate manually."
        )
    text = address.strip()
    if not text:
        return []

    places: list[GeocodedPlace] = []
    for row in _nominatim_results(settings, text, client=client):
        if not isinstance(row, dict):
            continue
        try:
            point = assert_within_operating_area(
                Coordinates(latitude=float(row["lat"]), longitude=float(row["lon"]))  # type: ignore[arg-type]
            )
        except KeyError, TypeError, ValueError, CoordinateOutOfRangeError:
            # A malformed row, or a match somewhere else in the world: not this clinic.
            continue
        places.append(
            GeocodedPlace(label=str(row.get("display_name") or text), point=point)
        )
    if not places:
        logger.info(
            "Geocoding returned no candidate inside %s for a typed address.",
            OPERATING_COUNTRY,
        )
    return places
