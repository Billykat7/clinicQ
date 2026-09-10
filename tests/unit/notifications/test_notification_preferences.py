"""Unit tests for notification preference resolution and unsubscribe (Issue 72 / M11).

The preference layer is the one place a send is checked against the recipient's wishes, and two of
its rules are load-bearing enough to test in isolation (per ``.cursor/rules/testing-strategy.mdc``,
unit tests are reserved for exactly this kind of essential logic):

* essential mail is never dropped — an essential category set to ``off`` is coerced to email, and
  its channel is always allowed;
* a non-urgent message in the recipient's quiet hours is *deferred* to the end of the window (not
  dropped), while an urgent one and one outside the window go straight out; a non-essential
  category the user turned off is suppressed;
* login-free unsubscribe is enumeration-safe — an unknown address reports the same success as a
  known one, an invalid token is rejected, and it is idempotent.

Timezone/JWT settings are pinned so a developer's local ``.env`` cannot change outcomes.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import (
    AppEnvironment,
    NotificationCategory,
    NotificationChannel,
    NotificationChannelPreference,
    NotificationTemplate,
)
from src.core.config import Settings
from src.core.s3_logging import APP_TIMEZONE
from src.core.security import create_unsubscribe_token
from src.database.models import Base
from src.database.models.notification_preference import NotificationPreference
from src.database.models.user import User
from src.database.schema import sqlite_schema_translate_map
from src.modules.notifications import preferences
from src.modules.notifications.preferences import DeliveryOutcome, InvalidTimezoneError
from src.modules.notifications.schemas import NotificationPreferencesUpdate

_EMAIL = "resident@example.com"


@pytest.fixture(autouse=True)
def _pin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Settings so a developer's local ``.env`` cannot change token/link outcomes.

    Only the token (``src.core.security``) and unsubscribe-link (``preferences``) paths read
    settings; both bindings are replaced with a pinned instance carrying a known JWT secret and a
    public base URL (so an unsubscribe link is actually minted).
    """
    settings = Settings(
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="unit-test-secret-key-at-least-32-characters",
        public_base_url="https://properties.example.com",
    )
    import src.core.security as security_module

    monkeypatch.setattr(security_module, "get_settings", lambda: settings)
    monkeypatch.setattr(preferences, "get_settings", lambda: settings)


@pytest.fixture
def db() -> Generator[Session]:
    """In-memory SQLite session with the ORM tables (schema mapped away for SQLite)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _add_user(db: Session, email: str = _EMAIL) -> User:
    """Insert and return a user with ``email``."""
    user = User(email=email)
    db.add(user)
    db.flush()
    return user


def _set_pref(
    db: Session,
    user: User,
    *,
    channels: dict[str, str] | None = None,
    start: time | None = None,
    end: time | None = None,
    timezone: str = APP_TIMEZONE.key,
) -> NotificationPreference:
    """Create the user's preference row with the given channel map / quiet hours."""
    pref = NotificationPreference(
        user_id=user.id,
        channel_by_category=channels or {},
        quiet_hours_start=start,
        quiet_hours_end=end,
        timezone=timezone,
    )
    db.add(pref)
    db.flush()
    return pref


def _resolve(
    db: Session, template: NotificationTemplate, *, now: datetime | None = None
):
    """Resolve an email send to ``_EMAIL`` for ``template``."""
    return preferences.resolve(
        db,
        recipient_email=_EMAIL,
        template=template,
        channel=NotificationChannel.EMAIL,
        now=now,
    )


# --- Channel gating & essential guarantee ------------------------------------


def test_new_user_defaults_to_email_send(db: Session) -> None:
    """A recipient with no preference row receives everything by email (sensible default)."""
    _add_user(db)
    decision = _resolve(db, NotificationTemplate.MAINTENANCE_COMPLETED)
    assert decision.outcome is DeliveryOutcome.SEND
    assert decision.channel is NotificationChannel.EMAIL


def test_non_essential_off_is_suppressed(db: Session) -> None:
    """A non-essential category turned off suppresses the email."""
    user = _add_user(db)
    _set_pref(db, user, channels={NotificationCategory.MAINTENANCE.value: "off"})
    decision = _resolve(db, NotificationTemplate.MAINTENANCE_COMPLETED)
    assert decision.outcome is DeliveryOutcome.SUPPRESS
    assert decision.reason == "opted-out"


def test_non_essential_on_other_channel_suppresses_email(db: Session) -> None:
    """Choosing SMS for a category suppresses that category's email."""
    user = _add_user(db)
    _set_pref(db, user, channels={NotificationCategory.MAINTENANCE.value: "sms"})
    decision = _resolve(db, NotificationTemplate.MAINTENANCE_COMPLETED)
    assert decision.outcome is DeliveryOutcome.SUPPRESS
    assert decision.reason == "other-channel"


def test_essential_off_is_coerced_and_still_sent(db: Session) -> None:
    """An essential category stored as 'off' is coerced to email and still delivered."""
    user = _add_user(db)
    _set_pref(db, user, channels={NotificationCategory.FINANCIAL.value: "off"})
    decision = _resolve(db, NotificationTemplate.LEASE_EXPIRY_REMINDER)
    assert decision.outcome is DeliveryOutcome.SEND
    assert decision.channel is NotificationChannel.EMAIL
    assert decision.essential is True
    assert (
        preferences.chosen_channel(
            preferences.load_preference(db, _EMAIL), NotificationCategory.FINANCIAL
        )
        is NotificationChannelPreference.EMAIL
    )


# --- Quiet hours --------------------------------------------------------------


def _now_local(hour: int, minute: int = 0) -> datetime:
    """A business-timezone datetime today at the given local wall-clock time."""
    return datetime.now(APP_TIMEZONE).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def test_quiet_hours_defers_non_urgent(db: Session) -> None:
    """A non-urgent message inside quiet hours is deferred to the end of the window."""
    user = _add_user(db)
    _set_pref(db, user, start=time(22, 0), end=time(7, 0))
    now = _now_local(23, 30)  # inside 22:00–07:00
    decision = _resolve(db, NotificationTemplate.MAINTENANCE_COMPLETED, now=now)
    assert decision.outcome is DeliveryOutcome.DEFER
    assert decision.defer_until is not None
    assert decision.defer_until > now
    assert decision.defer_until.astimezone(APP_TIMEZONE).time() == time(7, 0)


def test_quiet_hours_does_not_defer_urgent(db: Session) -> None:
    """An urgent message (sign-in code) is delivered even inside quiet hours."""
    user = _add_user(db)
    _set_pref(db, user, start=time(22, 0), end=time(7, 0))
    now = _now_local(23, 30)
    decision = _resolve(db, NotificationTemplate.PASSWORD_RESET, now=now)
    assert decision.outcome is DeliveryOutcome.SEND


def test_outside_quiet_hours_sends(db: Session) -> None:
    """A non-urgent message outside the quiet window is sent immediately."""
    user = _add_user(db)
    _set_pref(db, user, start=time(22, 0), end=time(7, 0))
    now = _now_local(12, 0)
    decision = _resolve(db, NotificationTemplate.MAINTENANCE_COMPLETED, now=now)
    assert decision.outcome is DeliveryOutcome.SEND


def test_quiet_hours_same_day_window(db: Session) -> None:
    """A non-wrapping window (e.g. 09:00–17:00) defers a message inside it to 17:00."""
    user = _add_user(db)
    _set_pref(db, user, start=time(9, 0), end=time(17, 0))
    now = _now_local(10, 0)
    decision = _resolve(db, NotificationTemplate.APPLICATION_ACCEPTED, now=now)
    assert decision.outcome is DeliveryOutcome.DEFER
    assert decision.defer_until.astimezone(APP_TIMEZONE).time() == time(17, 0)


# --- Unsubscribe (enumeration-safe) ------------------------------------------


def test_unsubscribe_turns_category_off(db: Session) -> None:
    """A valid unsubscribe token turns the recipient's category off (and audits it)."""
    user = _add_user(db)
    token = create_unsubscribe_token(_EMAIL, NotificationCategory.MARKETING.value)
    result = preferences.apply_unsubscribe(db, token)
    assert result.valid and result.changed
    pref = preferences.load_preference(db, _EMAIL)
    assert (
        preferences.chosen_channel(pref, NotificationCategory.MARKETING)
        is NotificationChannelPreference.OFF
    )
    assert user.id == pref.user_id


def test_unsubscribe_is_idempotent(db: Session) -> None:
    """Unsubscribing an already-off category succeeds without a second change."""
    user = _add_user(db)
    _set_pref(db, user, channels={NotificationCategory.MARKETING.value: "off"})
    token = create_unsubscribe_token(_EMAIL, NotificationCategory.MARKETING.value)
    result = preferences.apply_unsubscribe(db, token)
    assert result.valid and not result.changed


def test_unsubscribe_essential_is_refused(db: Session) -> None:
    """An essential category cannot be unsubscribed — reported valid but unchanged."""
    _add_user(db)
    token = create_unsubscribe_token(_EMAIL, NotificationCategory.FINANCIAL.value)
    result = preferences.apply_unsubscribe(db, token)
    assert result.valid and result.essential and not result.changed


def test_unsubscribe_unknown_email_reports_success(db: Session) -> None:
    """A valid token for an address with no account still reports success (no enumeration)."""
    token = create_unsubscribe_token(
        "stranger@example.com", NotificationCategory.MARKETING.value
    )
    result = preferences.apply_unsubscribe(db, token)
    assert result.valid and not result.changed


def test_unsubscribe_invalid_token_is_rejected(db: Session) -> None:
    """A malformed/foreign token is rejected as invalid."""
    result = preferences.apply_unsubscribe(db, "not-a-real-token")
    assert not result.valid


def test_unsubscribe_url_none_for_essential(db: Session) -> None:
    """No unsubscribe link is minted for essential categories."""
    assert preferences.unsubscribe_url(_EMAIL, NotificationCategory.FINANCIAL) is None
    assert (
        preferences.unsubscribe_url(_EMAIL, NotificationCategory.MARKETING) is not None
    )


# --- Account-facing update ----------------------------------------------------


def test_update_coerces_essential_off_to_email(db: Session) -> None:
    """An update that sets an essential category off is stored as email instead."""
    user = _add_user(db)
    update = NotificationPreferencesUpdate(
        channels={NotificationCategory.ACCOUNT: NotificationChannelPreference.OFF}
    )
    view = preferences.update_preferences(db, user.id, update)
    account = next(
        c for c in view.categories if c.category is NotificationCategory.ACCOUNT
    )
    assert account.channel is NotificationChannelPreference.EMAIL


def test_update_rejects_unknown_timezone(db: Session) -> None:
    """An unknown IANA timezone is rejected."""
    user = _add_user(db)
    update = NotificationPreferencesUpdate(timezone="Mars/Olympus_Mons")
    with pytest.raises(InvalidTimezoneError):
        preferences.update_preferences(db, user.id, update)


def test_update_sets_and_clears_quiet_hours(db: Session) -> None:
    """Quiet hours can be set as a pair and cleared."""
    user = _add_user(db)
    preferences.update_preferences(
        db,
        user.id,
        NotificationPreferencesUpdate(
            quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0)
        ),
    )
    view = preferences.read_preferences(db, user.id)
    assert view.quiet_hours_start == time(22, 0)

    preferences.update_preferences(
        db, user.id, NotificationPreferencesUpdate(clear_quiet_hours=True)
    )
    cleared = preferences.read_preferences(db, user.id)
    assert cleared.quiet_hours_start is None and cleared.quiet_hours_end is None
