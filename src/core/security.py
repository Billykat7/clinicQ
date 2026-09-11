"""Security core: password hashing, refresh-token hashing, JWTs, and the staff identity (Issue 15).

Staff are the only people who hold an account; patients are a phone number and a one-time code
(Issue 17), never a row in ``user``. So this module is the identity every staff-facing route checks:

* **Passwords** are bcrypt hashes at cost 12 (``Settings.bcrypt_rounds``, refused below 12 outside
  development). The plaintext is never stored, returned or logged.
* **The access token** is a short-lived HS256 JWT typed ``access``. Every token this app signs
  shares ``JWT_SECRET`` and carries a ``type``, and each decoder accepts exactly one type, so a
  password-reset or unsubscribe link can never be presented as a session.
* **The refresh token** is an opaque 256-bit secret; only its SHA-256 digest is stored, so a copy of
  the database cannot mint a session.
* **One identity path.** :func:`find_active_user` is the single answer to "which account is this
  token, and may it still act": it resolves by the ``uid`` claim and refuses an account that has
  been deactivated or deleted, so a deactivation takes effect on the very next request rather than
  when the access token expires. :func:`get_current_staff` (the dependency), the RBAC gate and the
  scope resolver all go through it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import AssignmentScopeType, AuthScope, TokenType
from src.core.config import get_settings
from src.core.request_logging import bind_request_context
from src.database.models import User
from src.database.session import get_db

security_scheme = HTTPBearer(auto_error=False)

#: bcrypt reads at most 72 bytes of a password, and bcrypt 5 refuses longer input outright rather
#: than silently truncating it. The request schemas cap new passwords at this many UTF-8 bytes, so
#: the limit is a 422 the user can act on, never a 500 from the hash call.
BCRYPT_MAX_PASSWORD_BYTES = 72

# Default bcrypt cost factor (deliberate work factor for password storage). The effective
# cost is ``Settings.bcrypt_rounds`` — 12 in prod, optionally lower in development/tests to keep
# bcrypt-heavy suites fast (guarded so it can never drop below 12 outside development).
_BCRYPT_ROUNDS = 12


def _bcrypt_rounds() -> int:
    """Return the configured bcrypt cost, falling back to 12 if settings are unavailable."""
    try:
        return get_settings().bcrypt_rounds
    except Exception:
        return _BCRYPT_ROUNDS


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt at the configured cost. Plaintext must not be logged.

    Raises:
        ValueError: The password is longer than :data:`BCRYPT_MAX_PASSWORD_BYTES` UTF-8 bytes.
            Request schemas refuse such a password first (a 422), so this is a backstop that
            names the limit instead of bcrypt's own message.
    """
    if len(plain.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"A password may be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes long."
        )
    digest = bcrypt.hashpw(
        plain.encode("utf-8"),
        bcrypt.gensalt(rounds=_bcrypt_rounds()),
    )
    return digest.decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify plaintext against a stored bcrypt hash. False on mismatch or invalid hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hex digest of a refresh token for storage/lookup (not reversible)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    sub: str,
    email: str | None = None,
    uid: str | None = None,
    *,
    role: str | None = None,
    sites: Iterable[str] = (),
    sid: str | None = None,
) -> str:
    """Create a short-lived HS256 access JWT typed ``access``.

    Claims: ``sub``, ``type``, ``iat``, ``exp`` always; ``email``, ``uid``, ``role`` and ``sid`` when
    given; ``sites`` always (a sorted list, empty when the holder has no site). Use
    :func:`issue_access_token` to mint one for a signed-in account.

    ``sid`` is the session: the refresh-token family the token was minted for (Issue 16). The CSRF
    token is bound to it, and the sessions list marks the caller's own session by it.

    ``sub`` is the durable human identity (the email). ``uid`` is the ``user.id`` (a UUID) and is
    kept as a separate claim because audit attribution stores it in ``audit_event.actor_id``, which
    is a foreign key to ``user.id`` — the email in ``sub`` cannot satisfy that constraint.

    ``role`` and ``sites`` describe the holder for a client (the shell's greeting, a site switcher).
    **They are never an authorization input**: a token lives up to ``JWT_ACCESS_EXPIRE_MINUTES``,
    and a role or site withdrawn in that window must stop working at once, so RBAC and the site
    guard re-resolve both from the database on every request.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=settings.jwt_access_expire_minutes)
    payload: dict[str, Any] = {
        "sub": sub,
        "type": TokenType.ACCESS.value,
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
        "sites": sorted(set(sites)),
    }
    if email is not None:
        payload["email"] = email
    if uid is not None:
        payload["uid"] = uid
    if role is not None:
        payload["role"] = role
    if sid is not None:
        payload["sid"] = sid
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def site_ids_for(db: Session, user_id: str) -> list[str]:
    """Return the sites ``user_id`` holds an active role at, sorted.

    A staff member's site is a role assignment scoped to that site
    (:attr:`~src.commons.enums.AssignmentScopeType.SITE`), never a column on ``user``, so this reads
    the same active-assignment set RBAC resolves roles from (expired assignments excluded).
    """
    from src.core.rbac import (
        active_role_assignments,  # rbac imports models; keep it lazy
    )

    return sorted(
        {
            scope_id
            for _role, scope_type, scope_id in active_role_assignments(db, user_id)
            if scope_type == AssignmentScopeType.SITE.value and scope_id
        }
    )


def issue_access_token(db: Session, user: User, *, sid: str | None = None) -> str:
    """Mint the access token for a signed-in ``user``: identity, role, sites and session."""
    return create_access_token(
        sub=user.email,
        email=user.email,
        uid=str(user.id),
        role=user.role,
        sites=site_ids_for(db, str(user.id)),
        sid=sid,
    )


def create_activation_token(user_id: str, email: str) -> str:
    """Create a typed ACTIVATION JWT for the signup email link."""
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(hours=settings.activation_link_expire_hours)
    payload = {
        "sub": user_id,
        "email": email.lower(),
        "type": TokenType.ACTIVATION.value,
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def password_fingerprint(password_hash: str | None) -> str:
    """A keyed fingerprint of an account's current password hash (Issue 16).

    Carried by a password-reset link as ``pwv`` and compared with the account's hash when the link
    is used. A reset writes a new hash (bcrypt salts every hash, even for the same password), so
    the link stops matching the moment it has been used, or the password has changed any other way:
    a reset link is single-use without a table to remember it in. Keyed with the signing secret, so
    the fingerprint in a readable JWT says nothing about the hash.
    """
    key = hashlib.sha256(
        b"clinicq-pwv-v1|" + get_settings().jwt_secret.encode()
    ).digest()
    return hmac.new(key, (password_hash or "").encode(), hashlib.sha256).hexdigest()[
        :32
    ]


def create_password_reset_token(
    user_id: str, email: str, *, password_hash: str | None = None
) -> str:
    """Create a typed, single-use PASSWORD_RESET JWT for the reset-password link.

    ``password_hash`` is the account's current hash (``None`` for an account with no password);
    its :func:`password_fingerprint` rides as ``pwv`` so the link dies once used.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(hours=settings.password_reset_link_expire_hours)
    payload = {
        "sub": user_id,
        "email": email.lower(),
        "type": TokenType.PASSWORD_RESET.value,
        "pwv": password_fingerprint(password_hash),
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_email_change_token(user_id: str, new_email: str) -> str:
    """Create a typed EMAIL_CHANGE JWT proving control of a new address before it takes effect.

    The token carries the account id (``sub``) and the requested ``new_email`` so the confirm
    endpoint can apply the change without a second store; the account keeps its current address
    until this link is confirmed (Issue #59).
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(hours=settings.email_change_link_expire_hours)
    payload = {
        "sub": user_id,
        "new_email": new_email.lower(),
        "type": TokenType.EMAIL_CHANGE.value,
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_unsubscribe_token(email: str, category: str) -> str:
    """Create a long-lived, typed UNSUBSCRIBE JWT for a login-free unsubscribe link (Issue #72).

    Carries the recipient ``email`` and the notification ``category`` it unsubscribes, signed so the
    endpoint can act on it without a session and without a lookup that reveals whether an account
    exists. Long-lived because the link lives in an inbox for a long time.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(days=settings.unsubscribe_link_expire_days)
    payload = {
        "email": email.lower().strip(),
        "cat": category,
        "type": TokenType.UNSUBSCRIBE.value,
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT. Return the payload dict, or None if invalid/expired."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        return None


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an ACCESS JWT. Return the payload, or None.

    The only decoder a session may be read with. A validly signed token of any other type (an
    activation, reset or unsubscribe link) is refused, as is a token with no type at all.
    """
    payload = decode_token(token)
    if payload is None or payload.get("type") != TokenType.ACCESS.value:
        return None
    return payload


def session_id_from_access_token(token: str | None) -> str | None:
    """Return the ``sid`` of a genuinely signed access token, **ignoring its expiry**, or None.

    The CSRF check needs to know which session a request's access cookie belongs to even in the
    second before a route turns an expired token into a 401; the signature and the type are still
    verified, so the answer cannot be forged.
    """
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != TokenType.ACCESS.value:
        return None
    sid = payload.get("sid")
    return str(sid) if sid else None


def decode_activation_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an ACTIVATION JWT. Return the payload, or None."""
    payload = decode_token(token)
    if payload is None or payload.get("type") != TokenType.ACTIVATION.value:
        return None
    return payload


def decode_password_reset_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a PASSWORD_RESET JWT. Return the payload, or None."""
    payload = decode_token(token)
    if payload is None or payload.get("type") != TokenType.PASSWORD_RESET.value:
        return None
    return payload


def decode_email_change_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an EMAIL_CHANGE JWT. Return the payload, or None."""
    payload = decode_token(token)
    if payload is None or payload.get("type") != TokenType.EMAIL_CHANGE.value:
        return None
    return payload


def decode_unsubscribe_token(token: str) -> dict[str, Any] | None:
    """Decode and validate an UNSUBSCRIBE JWT. Return the payload (``email``/``cat``), or None."""
    payload = decode_token(token)
    if payload is None or payload.get("type") != TokenType.UNSUBSCRIBE.value:
        return None
    return payload


def _token_from_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    """Prefer an ``Authorization: Bearer`` token; fall back to the access cookie."""
    if credentials is not None and credentials.credentials:
        return credentials.credentials
    return request.cookies.get(get_settings().access_token_cookie_name)


def _bind_actor(payload: dict[str, Any]) -> None:
    """Put the signed-in user's id on the request's log context (Issue 6).

    The ``uid`` claim (``user.id``), never ``sub``: ``sub`` is the email address, and an email in
    every log line is personal data the logs have no need of. A token without ``uid`` binds nothing.
    """
    uid = payload.get("uid")
    if uid:
        bind_request_context(actor_id=str(uid))


async def get_current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(security_scheme)
    ] = None,
) -> dict[str, Any]:
    """Resolve the current session's token claims from a Bearer token or the access cookie.

    Only an ``access``-typed token is accepted (:func:`decode_access_token`). Returns the claims
    without touching the database; a route that needs the account itself, and needs it to still be
    allowed to act, depends on :func:`get_current_staff` instead (the RBAC dependencies do the same
    check through :func:`resolve_active_user`).

    Raises 401 when authentication is enabled and no valid token is present.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return {"sub": "anonymous", "scope": AuthScope.READ.value}

    token = _token_from_request(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _bind_actor(payload)
    return payload


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(security_scheme)
    ] = None,
) -> dict[str, Any] | None:
    """Resolve token claims when present; return None when there are no valid credentials."""
    settings = get_settings()
    if not settings.auth_enabled:
        return {"sub": "anonymous", "scope": AuthScope.READ.value}
    token = _token_from_request(request, credentials)
    if not token:
        return None
    payload = decode_access_token(token)
    if payload is not None:
        _bind_actor(payload)
    return payload


def find_active_user(db: Session, claims: Mapping[str, Any]) -> User | None:
    """Return the account a session's claims name, if it may still act; otherwise ``None``.

    The one resolution every authenticated path shares (Issue 15). By ``uid`` (the ``user.id``),
    which survives an email change; a token minted without one falls back to its email. An account
    that is deactivated (``is_active`` false) or deleted resolves to ``None``, so switching an
    account off takes effect on its next request, not when its access token expires.
    """
    uid = str(claims.get("uid") or "").strip()
    if uid:
        user = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
    else:
        email = str(claims.get("email") or claims.get("sub") or "").strip().lower()
        if not email or email == "anonymous":
            return None
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.is_active or user.is_deleted:
        return None
    return user


def resolve_active_user(db: Session, claims: Mapping[str, Any]) -> User:
    """Like :func:`find_active_user`, but raise 401 when the claims name no account that may act.

    One message for "no such account", "deactivated" and "deleted": the caller learns only that
    the session is no longer good, which is all a client can act on.
    """
    user = find_active_user(db, claims)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_staff(
    claims: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Dependency: the signed-in staff account, from a Bearer header or the access cookie.

    Staff are the only people with an account (patients have a session of their own, Issue 17), so
    this is the account behind an ``access`` token, resolved and checked by
    :func:`resolve_active_user`. A sync dependency: it reads the database, so FastAPI runs it in
    the worker pool rather than on the event loop.

    Raises 401 when there is no valid access token, or its account is unknown, deactivated or
    deleted.
    """
    return resolve_active_user(db, claims)


#: The signed-in staff account, for a route signature: ``staff: CurrentStaff``.
CurrentStaff = Annotated[User, Depends(get_current_staff)]
