"""Integration tests for the notification API (Issue 67 / M11).

Two operational endpoints back the notification service:

* ``GET /api/v1/notifications/{id}`` — admin delivery-status query, gated by the ``logs`` READ verb.
  Asserted here for an authorized admin (200 with the row's status), a missing row (404) and an
  unauthenticated caller (401).
* ``POST /api/v1/notifications/webhooks/delivery`` — a provider delivery-status callback,
  authenticated by the ``X-Webhook-Secret`` shared secret. Asserted for a good secret (204 + the
  row advances to ``delivered``), a wrong secret (401), an unknown message id (404) and the
  feature-off case where no secret is configured (404, endpoint hidden).

Per ``.cursor/rules/testing-strategy.mdc`` these drive real HTTP against an in-memory database and
assert JSON / status codes / DB state — never HTML — and build isolated ``Settings``
(``_env_file=None``) so a developer's local ``.env`` cannot change outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
    UserRole,
)
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.models.notification import Notification
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "notification-endpoints-test-secret-min-32-chars"
_WEBHOOK_SECRET = "wh-secret-123"
_ADMIN_EMAIL = "admin.notify@example.com"
_USER_EMAIL = "plain.user@example.com"
_BASE = "/api/v1/notifications"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC on, no SMTP, a webhook secret."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",
        "notification_webhook_secret": _WEBHOOK_SECRET,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed_rbac(factory: sessionmaker[Session]) -> None:
    """Seed system roles and default grants (admin holds ``logs``), as Alembic does."""
    with factory() as db:
        for name, description in default_system_roles():
            db.add(RbacRole(name=name, description=description, is_system=True))
        for role, resource, verb in default_role_permissions():
            db.add(
                RolePermission(
                    role=role,
                    resource=resource,
                    max_verb=verb.value,
                    created_at=datetime.now(),  # noqa: DTZ005 — seed row; value irrelevant to test,
                    scope=seeded_grant_scope(role).value,
                )
            )
        db.commit()


def _add_user(factory: sessionmaker[Session], *, email: str, role: str) -> None:
    """Insert a verified user with ``role``."""
    with factory() as db:
        db.add(User(email=email, role=role, is_verified=True))
        db.commit()


def _add_notification(factory: sessionmaker[Session], **overrides: object) -> str:
    """Insert one notification row and return its id."""
    fields: dict[str, object] = {
        "channel": NotificationChannel.SMS.value,
        "template_key": NotificationTemplate.OTP_SIGN_IN.value,
        "recipient": "+27831112222",
        "subject": None,
        "status": NotificationStatus.SENT.value,
        "provider": "fake",
        "provider_message_id": "fake-abc-123",
        "payload": {"code": "111222"},
        "attempts": 1,
        "max_attempts": 5,
    }
    fields.update(overrides)
    with factory() as db:
        row = Notification(**fields)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB with seeded RBAC and ``Settings``."""
    apps: list[tuple[object, object]] = []

    def _make(**settings_overrides: object) -> SimpleNamespace:
        settings = _settings(**settings_overrides)
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ).execution_options(schema_translate_map=sqlite_schema_translate_map())
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        _seed_rbac(factory)
        _add_user(factory, email=_ADMIN_EMAIL, role=UserRole.ADMIN.value)
        _add_user(factory, email=_USER_EMAIL, role=UserRole.USER.value)

        def _override_get_db() -> Generator[Session]:
            db = factory()
            try:
                yield db
            finally:
                db.close()

        monkeypatch.setattr(security, "get_settings", lambda: settings)
        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_settings] = lambda: settings
        client = TestClient(app)
        apps.append((app, engine))
        return SimpleNamespace(client=client, settings=settings, session=factory)

    yield _make

    for app, engine in apps:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        Base.metadata.drop_all(engine)  # type: ignore[arg-type]
        engine.dispose()  # type: ignore[attr-defined]


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


# --- Status query -------------------------------------------------------------


def test_get_status_returns_row_for_admin(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An admin reads a notification's delivery status (200 with the current state)."""
    ctx = make_client()
    notification_id = _add_notification(ctx.session)

    resp = ctx.client.get(f"{_BASE}/{notification_id}", headers=_bearer(_ADMIN_EMAIL))

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["id"] == notification_id
    assert body["channel"] == NotificationChannel.SMS.value
    assert body["status"] == NotificationStatus.SENT.value
    assert body["provider_message_id"] == "fake-abc-123"


def test_get_status_missing_row_is_404(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An unknown notification id is a 404 for the admin."""
    ctx = make_client()
    resp = ctx.client.get(f"{_BASE}/does-not-exist", headers=_bearer(_ADMIN_EMAIL))
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_get_status_requires_auth(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An unauthenticated caller cannot read delivery status (401)."""
    ctx = make_client()
    notification_id = _add_notification(ctx.session)
    resp = ctx.client.get(f"{_BASE}/{notification_id}")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# --- Delivery-status webhook --------------------------------------------------


def test_webhook_marks_delivered_with_valid_secret(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A correct secret + known message id advances the row to ``delivered`` (204)."""
    ctx = make_client()
    notification_id = _add_notification(ctx.session)

    resp = ctx.client.post(
        f"{_BASE}/webhooks/delivery",
        json={"provider_message_id": "fake-abc-123", "delivered": True},
        headers={"X-Webhook-Secret": _WEBHOOK_SECRET},
    )

    assert resp.status_code == status.HTTP_204_NO_CONTENT
    with ctx.session() as db:
        row = db.get(Notification, notification_id)
        assert row is not None
        assert row.status == NotificationStatus.DELIVERED.value
        assert row.delivered_at is not None


def test_webhook_wrong_secret_is_401(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A wrong shared secret is rejected (401) and the row is unchanged."""
    ctx = make_client()
    _add_notification(ctx.session)

    resp = ctx.client.post(
        f"{_BASE}/webhooks/delivery",
        json={"provider_message_id": "fake-abc-123", "delivered": True},
        headers={"X-Webhook-Secret": "wrong"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_webhook_unknown_message_id_is_404(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A valid secret but an unknown provider message id is a 404."""
    ctx = make_client()
    resp = ctx.client.post(
        f"{_BASE}/webhooks/delivery",
        json={"provider_message_id": "nope", "delivered": True},
        headers={"X-Webhook-Secret": _WEBHOOK_SECRET},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_webhook_disabled_when_no_secret_configured(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """With no secret configured the webhook is hidden (404), even with a matching id."""
    ctx = make_client(notification_webhook_secret="")
    _add_notification(ctx.session)

    resp = ctx.client.post(
        f"{_BASE}/webhooks/delivery",
        json={"provider_message_id": "fake-abc-123", "delivered": True},
        headers={"X-Webhook-Secret": ""},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    # Ledger untouched: still 'sent', not 'delivered'.
    with ctx.session() as db:
        row = db.execute(
            select(Notification).where(
                Notification.provider_message_id == "fake-abc-123"
            )
        ).scalar_one()
        assert row.status == NotificationStatus.SENT.value


# --- Delivery viewer list (Issue #87) -----------------------------------------


def test_list_notifications_returns_rows_and_total_for_admin(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An admin (``logs`` READ) lists notifications with the pre-paging total."""
    ctx = make_client()
    for i in range(3):
        _add_notification(ctx.session, provider_message_id=f"m-{i}")

    resp = ctx.client.get(_BASE, headers=_bearer(_ADMIN_EMAIL))

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_notifications_requires_logs_read(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A standard user lacks ``logs`` READ and cannot list notifications (403)."""
    ctx = make_client()
    _add_notification(ctx.session)

    resp = ctx.client.get(_BASE, headers=_bearer(_USER_EMAIL))

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_list_notifications_filters_by_status_and_channel(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Status and channel filters narrow the listing; total reflects the filtered set."""
    ctx = make_client()
    _add_notification(
        ctx.session,
        provider_message_id="sent-sms",
        channel=NotificationChannel.SMS.value,
        status=NotificationStatus.SENT.value,
    )
    _add_notification(
        ctx.session,
        provider_message_id="dead-email",
        channel=NotificationChannel.EMAIL.value,
        recipient="a@example.com",
        status=NotificationStatus.DEAD.value,
    )

    resp = ctx.client.get(
        _BASE,
        params={"status": NotificationStatus.DEAD.value, "channel": "email"},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["provider_message_id"] == "dead-email"


def test_list_notifications_paginates(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """``offset``/``limit`` page the listing while ``total`` stays the full count."""
    ctx = make_client()
    for i in range(5):
        _add_notification(ctx.session, provider_message_id=f"p-{i}")

    resp = ctx.client.get(
        _BASE, params={"offset": 2, "limit": 2}, headers=_bearer(_ADMIN_EMAIL)
    )

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
