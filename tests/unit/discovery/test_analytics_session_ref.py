"""The discovery session reference and the recorder's failure path (Issue 38), without a database."""

from datetime import date
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from src.commons.enums import (
    AppEnvironment,
    DiscoveryChannel,
    DistanceBasis,
    SectorFilter,
)
from src.core.config import Settings
from src.modules.discovery import analytics
from src.modules.discovery.analytics import SiteConversion

_SETTINGS = Settings(
    _env_file=None,  # type: ignore[call-arg]
    environment=AppEnvironment.DEVELOPMENT,
    jwt_secret="m5-analytics-unit-secret-min-32-characters",
)
_MONDAY = date(2026, 9, 14)


def test_no_session_is_no_reference() -> None:
    """A caller without the cookie is counted, and linked to nothing."""
    assert analytics.session_ref(None, _MONDAY, _SETTINGS) is None
    assert analytics.session_ref("", _MONDAY, _SETTINGS) is None


def test_the_reference_is_stable_within_a_day_and_rotates_across_days() -> None:
    """Grouping one visit is the point; linking two days is exactly what it must not allow."""
    token = analytics.new_session_token()
    monday = analytics.session_ref(token, _MONDAY, _SETTINGS)
    assert monday == analytics.session_ref(token, _MONDAY, _SETTINGS)
    assert monday != analytics.session_ref(token, date(2026, 9, 15), _SETTINGS)
    assert monday is not None and token not in monday
    assert len(monday) == analytics.SESSION_REF_HEX


def test_the_reference_depends_on_the_server_key() -> None:
    """Without the application secret, a token cannot be turned into the stored reference."""
    token = analytics.new_session_token()
    other = _SETTINGS.model_copy(
        update={"jwt_secret": "another-secret-of-at-least-32-chars!!"}
    )
    assert analytics.session_ref(token, _MONDAY, _SETTINGS) != analytics.session_ref(
        token, _MONDAY, other
    )


def test_new_tokens_do_not_repeat() -> None:
    assert len({analytics.new_session_token() for _ in range(1000)}) == 1000


def test_a_failure_to_record_is_dropped_and_never_raised() -> None:
    """Recording never breaks a patient's search: the error is logged, the session rolled back."""
    db = MagicMock()
    db.begin_nested.side_effect = OperationalError("INSERT", {}, Exception("disk full"))

    recorded = analytics.record_search(
        db,
        channel=DiscoveryChannel.API,
        session_token="token",
        sector=SectorFilter.ALL,
        origin_basis=DistanceBasis.POSITION,
        radius_m=10_000,
        result_count=3,
        settings=_SETTINGS,
    )

    assert recorded is None
    db.rollback.assert_called_once()


def test_switched_off_touches_no_session() -> None:
    db = MagicMock()
    off = _SETTINGS.model_copy(update={"discovery_analytics_enabled": False})
    assert (
        analytics.record_clinic_viewed(
            db, "site", channel=DiscoveryChannel.WEB, session_token="t", settings=off
        )
        is None
    )
    assert not db.mock_calls


def test_a_rate_of_nothing_is_not_zero() -> None:
    nothing = SiteConversion(
        "s", _MONDAY, _MONDAY, views=0, joins_started=0, joins_completed=0
    )
    some = SiteConversion(
        "s", _MONDAY, _MONDAY, views=8, joins_started=3, joins_completed=2
    )
    assert nothing.conversion_rate is None
    assert some.conversion_rate == 0.25
