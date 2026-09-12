"""Passwords and refresh tokens cannot be recovered from the database (Issue 15).

Proven against a PostgreSQL database built by the real migrations, by reading **raw rows** with
plain SQL rather than through the models, after the real HTTP flows that write credentials: a
password sign-in (a refresh row), a password change (a new hash) and a refresh (a rotated row). At
the production bcrypt cost, 12, not the reduced cost the rest of the suite hashes at.

Three things are checked:

1. the only credential columns on ``user`` and ``refresh_token`` are ``password`` (a bcrypt hash,
   cost 12) and ``token_hash`` (the SHA-256 digest of the cookie value): no other column exists to
   hold a copy;
2. no value in **any** table of the schema contains a plaintext password or a raw refresh token,
   so a copy cannot hide in an audit diff or a notification payload either;
3. the plaintext never reaches a log record on the way.
"""

import hashlib
import logging
import re
from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

import src.database.session as session_module
from src.commons.enums import AppEnvironment, DbSchema
from src.core import refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.database.session import get_db
from src.main import create_app
from tests.factories import StaffFactory

pytestmark = pytest.mark.postgres

_SCHEMA = DbSchema.CLINICQ.value
_FIRST_PASSWORD = "first-Plaintext-Pa55"
_SECOND_PASSWORD = "second-Plaintext-Pa55"
#: A bcrypt hash at cost 12: ``$2b$12$`` then 22 salt and 31 hash characters.
_BCRYPT_12 = re.compile(r"^\$2b\$12\$[./A-Za-z0-9]{53}$")


class _Everything(logging.Handler):
    """Keep every record, at every level, from every logger — unredacted, as the app emits it."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep the record."""
        self.records.append(record)

    def text(self) -> str:
        """Every message and every ``extra`` value, as one searchable string."""
        return "\n".join(
            f"{record.getMessage()} {vars(record)!r}" for record in self.records
        )


@pytest.fixture
def logs() -> Iterator[_Everything]:
    """Capture the root logger at DEBUG for the length of the test."""
    handler = _Everything()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.fixture
def ctx(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[SimpleNamespace]:
    """The real app on the migrated database, hashing at the production cost of 12."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="credentials-at-rest-test-secret-min-32-chars",
        auth_password_login_enabled=True,
        smtp_host="",
    )
    factory = sessionmaker(bind=migrated_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(session_module, "_engine", migrated_engine)
    monkeypatch.setattr(session_module, "_session_local", factory)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(refresh_token_policy, "get_settings", lambda: settings)
    # Undo the suite-wide speed-up: this test is about the hash production writes.
    monkeypatch.setattr(security, "_bcrypt_rounds", lambda: 12)

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    yield SimpleNamespace(
        client=TestClient(app), settings=settings, engine=migrated_engine
    )
    app.dependency_overrides.clear()


def _every_value_in_the_schema(engine: Engine) -> list[tuple[str, str, str]]:
    """``(table, column, value)`` for every non-null value of every table in the schema, as text."""
    out: list[tuple[str, str, str]] = []
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
            ),
            {"schema": _SCHEMA},
        ).scalars()
        for table in list(tables):
            result = conn.execute(text(f'SELECT * FROM {_SCHEMA}."{table}"'))
            columns = list(result.keys())
            for row in result:
                out.extend(
                    (table, column, str(value))
                    for column, value in zip(columns, row, strict=True)
                    if value is not None
                )
    return out


def _columns(engine: Engine, table: str) -> set[str]:
    """The column names of one table, from the catalog."""
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": _SCHEMA, "table": table},
            ).scalars()
        )


def test_no_password_or_refresh_token_can_be_read_back_from_the_database(
    ctx: SimpleNamespace, logs: _Everything
) -> None:
    """Sign in, change the password, refresh; then read every raw row and every log record."""
    with Session(ctx.engine) as db:
        staff = StaffFactory.create(
            db, password=security.hash_password(_FIRST_PASSWORD)
        )
        db.commit()
        email, user_id = staff.email, staff.id

    client: TestClient = ctx.client
    signed_in = client.post(
        "/api/v1/auth/password/login",
        json={"email": email, "password": _FIRST_PASSWORD},
    )
    assert signed_in.status_code == 200, signed_in.text
    first_refresh = client.cookies.get(ctx.settings.refresh_token_cookie_name)
    access = client.cookies.get(ctx.settings.access_token_cookie_name)

    changed = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": _FIRST_PASSWORD, "new_password": _SECOND_PASSWORD},
        headers={"X-CSRF-Token": client.cookies.get(ctx.settings.csrf_cookie_name)},
    )
    assert changed.status_code == 200, changed.text
    refreshed = client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": client.cookies.get(ctx.settings.csrf_cookie_name)},
    )
    assert refreshed.status_code == 200, refreshed.text
    second_refresh = client.cookies.get(ctx.settings.refresh_token_cookie_name)
    assert (
        first_refresh and second_refresh and access and first_refresh != second_refresh
    )

    # 1. The only credential columns are the bcrypt hash and the SHA-256 digest.
    credential_like = re.compile(r"password|secret|token|otp|pin", re.IGNORECASE)
    assert {c for c in _columns(ctx.engine, "user") if credential_like.search(c)} == {
        "password"
    }
    assert {
        c for c in _columns(ctx.engine, "refresh_token") if credential_like.search(c)
    } == {"token_hash"}
    with ctx.engine.connect() as conn:
        stored = conn.execute(
            text(f'SELECT password FROM {_SCHEMA}."user" WHERE id = :id'),
            {"id": user_id},
        ).scalar_one()
        digests = set(
            conn.execute(
                text(
                    f"SELECT token_hash FROM {_SCHEMA}.refresh_token WHERE user_id = :id"
                ),
                {"id": user_id},
            ).scalars()
        )
    assert _BCRYPT_12.match(stored), stored[:7]
    assert security.verify_password(_SECOND_PASSWORD, stored)
    assert not security.verify_password(_FIRST_PASSWORD, stored)
    assert {
        hashlib.sha256(first_refresh.encode()).hexdigest(),
        hashlib.sha256(second_refresh.encode()).hexdigest(),
    } <= digests

    # 2. No copy of a secret anywhere in the schema, in any table or column.
    secrets = {
        "first password": _FIRST_PASSWORD,
        "second password": _SECOND_PASSWORD,
        "first refresh token": first_refresh,
        "second refresh token": second_refresh,
    }
    values = _every_value_in_the_schema(ctx.engine)
    assert len(values) > 20  # the scan really read rows
    for label, secret in secrets.items():
        found = [(table, column) for table, column, value in values if secret in value]
        assert not found, f"{label} is readable in {found}"

    # 3. And none of them reached a log record, redacted or otherwise.
    captured = logs.text()
    assert captured  # the flow did log (requests, sign-in)
    for label, secret in {**secrets, "access token": access}.items():
        assert secret not in captured, f"{label} reached a log record"
