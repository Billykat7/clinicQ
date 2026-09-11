"""The audit trail: append-only in the database, site-scoped to read, joinable to the logs (Issue 20).

Two halves:

* **PostgreSQL** (marked ``postgres``): the append-only guarantee is the database's, not the
  application's, so it is proven with raw SQL — an ``UPDATE``, a ``DELETE`` and a ``TRUNCATE`` are
  each refused and the row is still there afterwards.
* **HTTP**: a clinic manager reads their own clinic's trail and gets 404 for another's; the
  platform-wide read sees every clinic; each read is itself audited; and every row carries the
  request id that caused it, so the trail joins to that request's log lines.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    AssignmentScopeType,
    AuditAction,
    AuditEntityType,
    DbSchema,
    UserRole,
)
from src.core import refresh_token_policy, security
from src.core.audit import record_audit_event
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.core.site_scope import CROSS_SITE_REASON_HEADER
from src.database.models import AuditEvent, Base, User, UserRoleAssignment
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory

_SCHEMA = DbSchema.CLINICQ.value
_SITE_A = "0199b0c0-0000-7000-8000-0000000000aa"
_SITE_B = "0199b0c0-0000-7000-8000-0000000000bb"


# --- the database refuses to change a row ------------------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("statement", "what"),
    [
        (f"UPDATE {_SCHEMA}.audit_event SET action = 'x'", "UPDATE"),
        (f"DELETE FROM {_SCHEMA}.audit_event", "DELETE"),
        (f"TRUNCATE {_SCHEMA}.audit_event", "TRUNCATE"),
    ],
)
def test_the_database_refuses_to_rewrite_the_trail(
    migrated_engine: Engine, statement: str, what: str
) -> None:
    """Append-only is the database's guarantee, so a compromised code path cannot undo it.

    ``TRUNCATE`` needs its own statement-level trigger (Issue 20): the baseline's row-level one
    never fires for it, so it would have emptied the table in one statement.
    """
    with Session(migrated_engine) as db:
        record_audit_event(
            db,
            action=AuditAction.UPDATE,
            entity_type=AuditEntityType.SITE,
            entity_id=_SITE_A,
            actor="nurse@clinicq.example",
            context="a fact of the record",
        )
        db.commit()

    with pytest.raises(DBAPIError) as refused, migrated_engine.begin() as conn:
        conn.execute(text(statement))
    assert "append-only" in str(refused.value).lower(), what

    with migrated_engine.connect() as conn:
        assert (
            conn.execute(
                text(f"SELECT count(*) FROM {_SCHEMA}.audit_event")
            ).scalar_one()
            == 1
        )


# --- reading the trail -------------------------------------------------------------------


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Two clinics with a manager each, an operator, and rows in both clinics' trails."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="audit-log-test-secret-min-32-characters!!",
        auth_password_login_enabled=True,
        smtp_host="",
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        sync_rbac_catalog(db)
        for name, site in (("a", _SITE_A), ("b", _SITE_B)):
            StaffFactory.create(
                db,
                email=f"manager.{name}@clinicq.example",
                role=UserRole.CLINIC_MANAGER,
                site_id=site,
            )
        StaffFactory.create(
            db, email="operator@clinicq.example", role=UserRole.PLATFORM_ADMIN
        )
        # One recorded fact per clinic, as a mutation at that clinic would leave.
        for site in (_SITE_A, _SITE_B):
            record_audit_event(
                db,
                action=AuditAction.UPDATE,
                entity_type=AuditEntityType.SITE,
                entity_id=site,
                actor=f"someone@{site}",
                site_id=site,
                context="display mode changed",
            )
        db.commit()

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(refresh_token_policy, "get_settings", lambda: settings)
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    def _client(email: str) -> TestClient:
        client = TestClient(app)
        signed_in = client.post(
            "/api/v1/auth/password/login",
            json={"email": email, "password": FACTORY_STAFF_PASSWORD},
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
        return client

    yield SimpleNamespace(app=app, session=factory, client=_client, settings=settings)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_a_manager_reads_their_own_clinics_trail_and_nothing_else(
    ctx: SimpleNamespace,
) -> None:
    """The read is site-scoped: Clinic A's rows for Clinic A's manager, 404 for Clinic B's."""
    manager = ctx.client("manager.a@clinicq.example")

    own = manager.get(f"/api/v1/sites/{_SITE_A}/audit/events")
    assert own.status_code == status.HTTP_200_OK, own.text
    sites = {event["site_id"] for event in own.json()["events"]}
    assert sites == {_SITE_A}

    assert (
        manager.get(f"/api/v1/sites/{_SITE_B}/audit/events").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_the_search_is_itself_audited_against_the_clinic_it_read(
    ctx: SimpleNamespace,
) -> None:
    """Reading the log leaves a row saying who read it, with which filter, and where."""
    manager = ctx.client("manager.a@clinicq.example")
    manager.get(
        f"/api/v1/sites/{_SITE_A}/audit/events",
        params={"action": AuditAction.UPDATE.value},
    )
    with ctx.session() as db:
        reads = (
            db.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_type == AuditEntityType.AUDIT_LOG.value
                )
            )
            .scalars()
            .all()
        )
    assert len(reads) == 1
    read = reads[0]
    assert read.action == AuditAction.READ.value
    assert read.actor == "manager.a@clinicq.example"
    assert read.site_id == _SITE_A
    assert "action=update" in (read.context or "")


def test_every_row_carries_the_request_that_caused_it(ctx: SimpleNamespace) -> None:
    """``request_id`` joins an audit row to that request's log lines (Issue 6)."""
    manager = ctx.client("manager.a@clinicq.example")
    response = manager.get(f"/api/v1/sites/{_SITE_A}/audit/events")
    request_id = response.headers["X-Request-ID"]

    with ctx.session() as db:
        read = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.AUDIT_LOG.value
            )
        ).scalar_one()
    assert read.request_id == request_id
    assert read.site_id == _SITE_A  # taken from the site the guard resolved


def test_the_platform_wide_read_sees_every_clinic_and_can_narrow_to_one(
    ctx: SimpleNamespace,
) -> None:
    """The operator's console reads across clinics; a clinic manager cannot reach that route."""
    operator = ctx.client("operator@clinicq.example")
    manager = ctx.client("manager.a@clinicq.example")

    everything = operator.get(
        "/api/v1/audit/events", params={"entity_type": AuditEntityType.SITE.value}
    )
    assert everything.status_code == status.HTTP_200_OK, everything.text
    assert {event["site_id"] for event in everything.json()["events"]} == {
        _SITE_A,
        _SITE_B,
    }

    one = operator.get("/api/v1/audit/events", params={"site_id": _SITE_B})
    assert {event["site_id"] for event in one.json()["events"]} == {_SITE_B}

    assert manager.get("/api/v1/audit/events").status_code == status.HTTP_403_FORBIDDEN


def test_a_platform_admin_reads_a_clinics_trail_only_through_the_audited_hatch(
    ctx: SimpleNamespace,
) -> None:
    """The per-clinic route obeys the same rule as every other site-scoped route (Issue 19)."""
    operator = ctx.client("operator@clinicq.example")
    path = f"/api/v1/sites/{_SITE_A}/audit/events"

    assert operator.get(path).status_code == status.HTTP_404_NOT_FOUND
    allowed = operator.get(path, headers={CROSS_SITE_REASON_HEADER: "incident 12"})
    assert allowed.status_code == status.HTTP_200_OK

    with ctx.session() as db:
        hatch = (
            db.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_type == AuditEntityType.SITE.value,
                    AuditEvent.action == AuditAction.READ.value,
                )
            )
            .scalars()
            .all()
        )
    assert len(hatch) == 1 and "incident 12" in (hatch[0].context or "")


def test_the_trail_is_filtered_by_date_and_by_actor(ctx: SimpleNamespace) -> None:
    """The filters the admin API is supposed to have, on the site-scoped route."""
    manager = ctx.client("manager.a@clinicq.example")
    base = f"/api/v1/sites/{_SITE_A}/audit/events"

    by_actor = manager.get(base, params={"actor": f"someone@{_SITE_A}"})
    assert by_actor.json()["total"] == 1
    assert (
        manager.get(base, params={"actor": "nobody@example.org"}).json()["total"] == 0
    )

    future = manager.get(base, params={"since": "2099-01-01T00:00:00+02:00"})
    assert future.json()["total"] == 0
    past = manager.get(base, params={"until": "2099-01-01T00:00:00+02:00"})
    assert past.json()["total"] >= 1


def test_a_role_at_another_clinic_does_not_open_this_clinics_trail(
    ctx: SimpleNamespace,
) -> None:
    """The verb is resolved with the roles held *here*: a manager elsewhere is a stranger."""
    with ctx.session() as db:
        manager_b = db.execute(
            select(User).where(User.email == "manager.b@clinicq.example")
        ).scalar_one()
        # They are a receptionist at Clinic A: present, but without the audit grant.
        db.add(
            UserRoleAssignment(
                user_id=manager_b.id,
                role=UserRole.RECEPTIONIST.value,
                scope_type=AssignmentScopeType.SITE.value,
                scope_id=_SITE_A,
            )
        )
        db.commit()

    visiting = ctx.client("manager.b@clinicq.example")
    refused = visiting.get(f"/api/v1/sites/{_SITE_A}/audit/events")

    assert (
        refused.status_code == status.HTTP_403_FORBIDDEN
    )  # at the site, but not for this
    assert visiting.get(f"/api/v1/sites/{_SITE_B}/audit/events").status_code == 200
