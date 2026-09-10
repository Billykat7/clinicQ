"""Integration tests for profile-picture upload, replace, remove and serving (Issue 130 / M23).

Exercises ``/api/v1/auth/me/avatar`` and ``/api/v1/auth/avatars/{token}`` at the HTTP layer
against an in-memory database and a temporary private storage directory. The properties that
matter here are security ones, so each is asserted directly:

* **Owner-only, structurally.** Every write acts on the row resolved from the caller's own token
  and there is no user parameter anywhere — so the test that one user's upload leaves another's
  untouched is checking a property of the *design*, not of a permission check.
* **The declared content type is not trusted.** A file is accepted on its magic bytes, and the
  declared type must agree with them; a script renamed ``avatar.png`` is a 415 before any decoder
  sees it.
* **Nothing of the original file survives.** The stored bytes are a square WEBP we re-encoded, so
  EXIF — including the GPS coordinates of the user's home — is gone.
* **A replaced picture stops serving.** Each upload gets a fresh token, so the previous URL 404s
  immediately; that is what makes the stable, session-free serving URL safe and cacheable.

Per ``.cursor/rules/testing-strategy.mdc`` these assert JSON, status codes, headers and stored
bytes — never HTML — and build isolated ``Settings`` (``_env_file=None``) so a developer's local
``.env`` cannot change outcomes.
"""

from collections.abc import Callable, Generator
from datetime import UTC, datetime
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "profile-picture-test-secret-min-32-characters"
_USER_EMAIL = "owner.avatar@example.com"
_OTHER_EMAIL = "other.avatar@example.com"

_AUTH_URL = "/api/v1/auth"
_AVATAR_URL = f"{_AUTH_URL}/me/avatar"


def _settings(tmp_dir: str, **overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with avatar storage in a temp dir."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",
        "avatar_storage_dir": tmp_dir,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed_rbac(factory: sessionmaker[Session]) -> None:
    """Seed the system roles and default grants, mirroring Alembic migration ``0004``."""
    with factory() as db:
        for name, description in default_system_roles():
            db.add(RbacRole(name=name, description=description, is_system=True))
        for role, resource, verb in default_role_permissions():
            db.add(
                RolePermission(
                    role=role,
                    resource=resource,
                    max_verb=verb.value,
                    created_at=datetime.now(UTC),
                    scope=seeded_grant_scope(role).value,
                )
            )
        db.commit()


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB and a temp avatar storage dir."""
    apps: list[tuple[object, object]] = []

    def _make(**settings_overrides: object) -> SimpleNamespace:
        storage_dir = str(tmp_path_factory.mktemp("avatars"))
        settings = _settings(storage_dir, **settings_overrides)
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ).execution_options(schema_translate_map=sqlite_schema_translate_map())
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        _seed_rbac(factory)

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
        return SimpleNamespace(
            client=client, settings=settings, session=factory, storage_dir=storage_dir
        )

    yield _make

    for app, engine in apps:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        Base.metadata.drop_all(engine)  # type: ignore[arg-type]
        engine.dispose()  # type: ignore[attr-defined]


# --- Test data helpers --------------------------------------------------------


def _add_user(factory: sessionmaker[Session], email: str) -> User:
    """Insert a verified user and return a detached row."""
    with factory() as db:
        user = User(email=email, role=UserRole.USER.value, is_verified=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _png_bytes(width: int = 800, height: int = 400, colour: str = "red") -> bytes:
    """Encode a plain PNG of the given size (deliberately non-square)."""
    buffer = BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


# EXIF tag ids used by the fixtures below (Pillow takes raw ids, not names).
_EXIF_MAKE = 0x010F
_EXIF_ORIENTATION = 0x0112
_EXIF_GPS_IFD = 0x8825


def _jpeg_with_gps(
    *, orientation: int = 1, size: tuple[int, int] = (600, 600)
) -> bytes:
    """Encode a JPEG carrying EXIF GPS coordinates — the metadata that must not survive.

    Also carries a camera make (an easy needle to grep the stored bytes for) and an orientation
    tag, so one fixture covers both "the metadata is gone" and "the rotation was applied".
    """
    exif = Image.Exif()
    exif[_EXIF_MAKE] = "TestCam"
    exif[_EXIF_ORIENTATION] = orientation
    gps = exif.get_ifd(_EXIF_GPS_IFD)
    gps[1] = "S"
    gps[2] = (Fraction(33), Fraction(58), Fraction(0))
    gps[3] = "E"
    gps[4] = (Fraction(18), Fraction(27), Fraction(0))
    buffer = BytesIO()
    Image.new("RGB", size, "blue").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _upload(
    ctx: SimpleNamespace,
    data: bytes,
    *,
    email: str = _USER_EMAIL,
    filename: str = "me.png",
    content_type: str = "image/png",
    headers: dict[str, str] | None = None,
) -> Any:
    """POST a profile picture and return the raw response."""
    return ctx.client.post(
        _AVATAR_URL,
        files={"file": (filename, data, content_type)},
        headers={**_bearer(email), **(headers or {})},
    )


def _stored_key(factory: sessionmaker[Session], email: str) -> str | None:
    """Return the persisted ``avatar_key`` for one user."""
    with factory() as db:
        return db.execute(
            select(User.avatar_key).where(User.email == email)
        ).scalar_one()


def _stored_files(storage_dir: str) -> list[Path]:
    """Every object currently in the avatar store."""
    return sorted(p for p in Path(storage_dir).rglob("*") if p.is_file())


# --- Upload -------------------------------------------------------------------


def test_upload_stores_a_square_webp_and_exposes_it_on_me(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An upload is re-encoded to a square WEBP and becomes the user's ``avatar_url``."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    response = _upload(ctx, _png_bytes())

    assert response.status_code == status.HTTP_200_OK
    url = response.json()["avatar_url"]
    key = _stored_key(ctx.session, _USER_EMAIL)
    assert key is not None
    assert url == f"/api/v1/auth/avatars/{key}"
    # The same URL comes back from /me, so the shell renders it without a second source.
    me = ctx.client.get(f"{_AUTH_URL}/me", headers=_bearer(_USER_EMAIL))
    assert me.json()["avatar_url"] == url

    files = _stored_files(ctx.storage_dir)
    assert len(files) == 1
    with Image.open(files[0]) as stored:
        assert stored.format == "WEBP"
        assert stored.width == stored.height  # cropped square, from a 800x400 upload
        assert stored.width <= ctx.settings.avatar_max_dimension_px


def test_upload_strips_exif_including_location(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A photo's GPS coordinates are the user's home address — they must not be stored."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    response = _upload(
        ctx, _jpeg_with_gps(), filename="me.jpg", content_type="image/jpeg"
    )

    assert response.status_code == status.HTTP_200_OK
    stored_path = _stored_files(ctx.storage_dir)[0]
    with Image.open(stored_path) as stored:
        assert not stored.getexif()
        assert "exif" not in stored.info
    # And nothing of the original bytes survives verbatim.
    assert b"TestCam" not in stored_path.read_bytes()


def test_upload_applies_the_exif_orientation_before_cropping(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A phone photo tagged "rotate 90" is stored the way it was seen, not sideways."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    # Orientation 6 means "rotate 90 CW to display", so a 600x300 file displays as 300x600 and
    # the square crop must come from the *displayed* image — 300 on a side, not 600.
    response = _upload(
        ctx,
        _jpeg_with_gps(orientation=6, size=(600, 300)),
        filename="me.jpg",
        content_type="image/jpeg",
    )

    assert response.status_code == status.HTTP_200_OK
    with Image.open(_stored_files(ctx.storage_dir)[0]) as stored:
        assert stored.size == (300, 300)


def test_upload_does_not_enlarge_a_small_picture(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A 64px upload stays 64px — enlarging only spends bytes on invented pixels."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    _upload(ctx, _png_bytes(width=64, height=64))

    with Image.open(_stored_files(ctx.storage_dir)[0]) as stored:
        assert stored.size == (64, 64)


def test_replacing_a_picture_retires_the_previous_url(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A new upload gets a new token, and the old object stops serving immediately."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    first_url = _upload(ctx, _png_bytes(colour="red")).json()["avatar_url"]

    second_url = _upload(ctx, _png_bytes(colour="green")).json()["avatar_url"]

    assert second_url != first_url
    assert len(_stored_files(ctx.storage_dir)) == 1  # the old object is deleted
    assert ctx.client.get(first_url).status_code == status.HTTP_404_NOT_FOUND
    assert ctx.client.get(second_url).status_code == status.HTTP_200_OK


def test_upload_supersedes_an_externally_hosted_avatar_url(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An uploaded picture is the one answer — the pasted URL does not linger behind it."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    ctx.client.patch(
        f"{_AUTH_URL}/me",
        json={"avatar_url": "https://example.test/me.png"},
        headers=_bearer(_USER_EMAIL),
    )

    url = _upload(ctx, _png_bytes()).json()["avatar_url"]

    assert url.startswith("/api/v1/auth/avatars/")
    with ctx.session() as db:
        stored = db.execute(select(User).where(User.email == _USER_EMAIL)).scalar_one()
        assert stored.avatar_url is None


# --- Validation ---------------------------------------------------------------


def test_non_image_bytes_are_rejected_by_signature(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A script that merely claims to be a PNG never reaches a decoder (415)."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    response = _upload(ctx, b"<?php echo 'hi'; ?>" + b"\x00" * 64)

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert _stored_files(ctx.storage_dir) == []
    assert _stored_key(ctx.session, _USER_EMAIL) is None


def test_declared_type_must_match_the_actual_bytes(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A real PNG mislabelled as a JPEG is refused: the two must agree (415)."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    response = _upload(ctx, _png_bytes(), content_type="image/jpeg")

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


def test_an_image_type_outside_the_allow_list_is_rejected(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A GIF is a real image, but not one of the three accepted types (415)."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="GIF")

    response = _upload(
        ctx, buffer.getvalue(), filename="me.gif", content_type="image/gif"
    )

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


def test_oversized_upload_is_rejected_before_it_is_buffered(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """The declared Content-Length is checked first, so an oversized body is never read (413)."""
    ctx = make_client(avatar_max_bytes=1024)
    _add_user(ctx.session, _USER_EMAIL)

    response = _upload(ctx, _png_bytes(width=1200, height=1200, colour="purple"))

    assert response.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert _stored_files(ctx.storage_dir) == []


def test_undecodable_image_is_unprocessable(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Bytes with a valid PNG signature that do not decode are a 422, not a 500."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    truncated = _png_bytes()[:40]

    response = _upload(ctx, truncated)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert _stored_files(ctx.storage_dir) == []


def test_upload_requires_authentication(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """No session, no upload (401)."""
    ctx = make_client()

    response = ctx.client.post(
        _AVATAR_URL, files={"file": ("me.png", _png_bytes(), "image/png")}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- Ownership ----------------------------------------------------------------


def test_a_user_can_only_change_their_own_picture(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """There is no user parameter to point elsewhere: each caller acts on their own row."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    _add_user(ctx.session, _OTHER_EMAIL)
    owner_url = _upload(ctx, _png_bytes(colour="red")).json()["avatar_url"]

    other_url = _upload(ctx, _png_bytes(colour="green"), email=_OTHER_EMAIL).json()[
        "avatar_url"
    ]

    assert other_url != owner_url
    # The first user's picture is untouched by the second user's upload and removal.
    ctx.client.delete(_AVATAR_URL, headers=_bearer(_OTHER_EMAIL))
    assert _stored_key(ctx.session, _USER_EMAIL) is not None
    assert ctx.client.get(owner_url).status_code == status.HTTP_200_OK


# --- Remove -------------------------------------------------------------------


def test_remove_reverts_to_the_initial_and_deletes_the_object(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Removing clears the URL, deletes the bytes, and stops the old URL serving."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    url = _upload(ctx, _png_bytes()).json()["avatar_url"]

    response = ctx.client.delete(_AVATAR_URL, headers=_bearer(_USER_EMAIL))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["avatar_url"] is None
    assert _stored_key(ctx.session, _USER_EMAIL) is None
    assert _stored_files(ctx.storage_dir) == []
    assert ctx.client.get(url).status_code == status.HTTP_404_NOT_FOUND


def test_remove_is_idempotent(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Removing a picture that is not there succeeds and changes nothing."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    response = ctx.client.delete(_AVATAR_URL, headers=_bearer(_USER_EMAIL))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["avatar_url"] is None


def test_remove_requires_authentication(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """No session, no removal (401)."""
    ctx = make_client()

    assert ctx.client.delete(_AVATAR_URL).status_code == status.HTTP_401_UNAUTHORIZED


# --- Serving ------------------------------------------------------------------


def test_serving_returns_the_webp_with_private_caching(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """The picture is served as WEBP, cacheable but never in a shared cache."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    url = _upload(ctx, _png_bytes()).json()["avatar_url"]

    response = ctx.client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "image/webp"
    assert "private" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(response.content)) as served:
        assert served.format == "WEBP"


def test_serving_needs_no_session(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An ``<img>`` cannot send an Authorization header; the unguessable token is the protection."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)
    url = _upload(ctx, _png_bytes()).json()["avatar_url"]

    assert ctx.client.get(url).status_code == status.HTTP_200_OK


@pytest.mark.parametrize(
    "token",
    [
        "00000000000000000000000000000000",  # well-formed but unknown
        "not-a-token",  # wrong shape
        "..%2f..%2fetc%2fpasswd",  # traversal attempt
        "0" * 31,  # right alphabet, wrong length
    ],
)
def test_unknown_or_malformed_tokens_are_a_plain_404(
    make_client: Callable[..., SimpleNamespace], token: str
) -> None:
    """Every miss looks identical, so nothing is learned from probing the endpoint."""
    ctx = make_client()

    response = ctx.client.get(f"{_AUTH_URL}/avatars/{token}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_users_without_a_picture_are_unaffected(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """No regression for the majority: ``/me`` simply reports no avatar."""
    ctx = make_client()
    _add_user(ctx.session, _USER_EMAIL)

    me = ctx.client.get(f"{_AUTH_URL}/me", headers=_bearer(_USER_EMAIL))

    assert me.status_code == status.HTTP_200_OK
    assert me.json()["avatar_url"] is None
