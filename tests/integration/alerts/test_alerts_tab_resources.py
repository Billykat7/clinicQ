"""Tab-level RBAC for the alerts console (Issue #145 / M26).

Real HTTP against an in-memory database, asserting JSON/status codes (never HTML), per
``docs/IDE/RULES/testing-strategy.mdc``. No prior test file covered ``GET /api/v1/alerts`` or the
``/alerts/drafts*`` endpoints at all (confirmed by grep before writing this), so every case here is
new coverage, not a migration of an existing one.

Covers:

* the Inbox/Sent/Deleted tabs, each independently gated on ``communications.alerts.{folder}`` READ
  — a coarse ``communications.alerts`` grant still reaches every tab (inheritance unaffected), a
  tab-scoped grant reaches only that tab;
* the Drafts tab (a real, separate ``AlertDraft`` table/endpoint set, unlike messages/announcements'
  shared drafts infra) gated on ``communications.alerts.drafts``;
* the ``send`` named action on ``communications.alerts.drafts`` — a real dispatch transition
  (draft → broadcast) independently grantable/revocable from the plain drafts CREATE verb, and
  seeded by default to ``admin``/``manager`` so introducing it does not regress their existing
  ability to send an alert draft (migration ``0059`` / ``default_communications_named_action_grants``).
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import seeded_grant_scope
from src.core.rbac_manifest_registry import ALL_MANIFESTS
from src.core.rbac_manifest_sync import sync_all_manifests
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "alerts-tab-resources-test-secret-min-32-chars"


_ADMIN_EMAIL = "admin.alerts@example.com"


_OTHER_EMAIL = (
    "other.alerts@example.com"  # a second user so GLOBAL audience isn't empty
)


_ALERTS_URL = "/api/v1/alerts"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC on and no SMTP."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB with the manifest catalog synced.

    Syncing ``ALL_MANIFESTS`` (Issue #149) populates the ``resources`` table with the Issue #145
    tab-level tree, exactly as ``python -m src.core.rbac_manifest_sync`` does in production — a
    test that skipped this would still pass (the string-keyed resolver falls back to a manifest-
    derived parent map when the table is empty), but syncing here exercises the real deploy path.
    """
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

        with factory() as db:
            sync_all_manifests(db, ALL_MANIFESTS)
            db.add(
                User(email=_ADMIN_EMAIL, role=UserRole.ADMIN.value, is_verified=True)
            )
            db.add(User(email=_OTHER_EMAIL, is_verified=True))
            db.commit()

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


def _add_role(
    factory: sessionmaker[Session],
    *,
    role: str,
    email: str,
    grants: list[tuple[str, str]],
) -> None:
    """Insert a custom role, a user holding it, and one cumulative grant per ``(resource, verb)``."""
    with factory() as db:
        db.add(RbacRole(name=role, description=role, is_system=False))
        db.add(User(email=email, role=role, is_verified=True))
        for resource, verb in grants:
            db.add(
                RolePermission(
                    role=role,
                    resource=resource,
                    max_verb=verb,
                    scope=seeded_grant_scope(role).value,
                )
            )
        db.commit()


def test_a_coarse_communications_alerts_grant_reaches_every_tab(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A role holding only the coarse ``communications.alerts`` READ reaches inbox/sent/deleted."""
    ctx = make_client()
    client: TestClient = ctx.client
    _add_role(
        ctx.session,
        role="alerts-coarse",
        email="coarse@example.com",
        grants=[("communications.alerts", "read")],
    )
    for folder in ("inbox", "sent", "deleted"):
        resp = client.get(
            _ALERTS_URL,
            params={"folder": folder},
            headers=_bearer("coarse@example.com"),
        )
        assert resp.status_code == status.HTTP_200_OK, (folder, resp.text)


def test_a_tab_scoped_role_reaches_only_that_tab(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A role granted only ``communications.alerts.sent`` READ reaches Sent, not Inbox/Deleted."""
    ctx = make_client()
    client: TestClient = ctx.client
    _add_role(
        ctx.session,
        role="alerts-sent-only",
        email="sentonly@example.com",
        grants=[("communications.alerts.sent", "read")],
    )
    ok = client.get(
        _ALERTS_URL, params={"folder": "sent"}, headers=_bearer("sentonly@example.com")
    )
    assert ok.status_code == status.HTTP_200_OK

    for folder in ("inbox", "deleted"):
        resp = client.get(
            _ALERTS_URL,
            params={"folder": folder},
            headers=_bearer("sentonly@example.com"),
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (folder, resp.text)


def test_no_grant_at_all_is_forbidden_on_every_tab(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A signed-in user with no ``communications.alerts`` grant is refused on every tab."""
    ctx = make_client()
    client: TestClient = ctx.client
    for folder in ("inbox", "sent", "deleted"):
        resp = client.get(
            _ALERTS_URL, params={"folder": folder}, headers=_bearer(_OTHER_EMAIL)
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, folder


def test_drafts_crud_requires_the_drafts_resource(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A role with only ``communications.alerts.inbox`` READ (not drafts) is refused on drafts."""
    ctx = make_client()
    client: TestClient = ctx.client
    _add_role(
        ctx.session,
        role="alerts-inbox-only",
        email="inboxonly@example.com",
        grants=[("communications.alerts.inbox", "read")],
    )
    resp = client.get(f"{_ALERTS_URL}/drafts", headers=_bearer("inboxonly@example.com"))
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_coarse_grant_still_reaches_drafts_via_inheritance(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A coarse ``communications.alerts`` CREATE grant still reaches drafts CRUD, unaffected."""
    ctx = make_client()
    client: TestClient = ctx.client
    _add_role(
        ctx.session,
        role="alerts-coarse-create",
        email="coarsecreate@example.com",
        grants=[("communications.alerts", "create")],
    )
    created = client.post(
        f"{_ALERTS_URL}/drafts",
        json={"subject": "Draft", "body": "In progress."},
        headers=_bearer("coarsecreate@example.com"),
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    listed = client.get(
        f"{_ALERTS_URL}/drafts", headers=_bearer("coarsecreate@example.com")
    )
    assert listed.status_code == status.HTTP_200_OK
    assert len(listed.json()) == 1


def test_send_requires_the_named_action_beyond_plain_drafts_create(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Holding ``communications.alerts.drafts`` CREATE lets you save a draft but not send it."""
    ctx = make_client()
    client: TestClient = ctx.client
    _add_role(
        ctx.session,
        role="alerts-drafts-only",
        email="draftsonly@example.com",
        grants=[("communications.alerts.drafts", "create")],
    )
    draft = client.post(
        f"{_ALERTS_URL}/drafts",
        json={
            "audience_type": "global",
            "subject": "Heads up",
            "body": "Body text.",
            "severity": "info",
        },
        headers=_bearer("draftsonly@example.com"),
    )
    assert draft.status_code == status.HTTP_201_CREATED, draft.text
    draft_id = draft.json()["id"]

    sent = client.post(
        f"{_ALERTS_URL}/drafts/{draft_id}/send",
        headers=_bearer("draftsonly@example.com"),
    )
    assert sent.status_code == status.HTTP_403_FORBIDDEN
