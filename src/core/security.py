"""Security core: password hashing, refresh-token hashing, JWTs, and auth deps.

Ported from the ``maps`` project. The access token is a short-lived HS256 JWT.
The refresh token is an opaque, URL-safe secret stored only as its SHA-256 hash.
Activation and password-reset links use typed JWTs (``type`` claim).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.commons.enums import AuthScope, TokenType
from src.core.config import get_settings
from src.core.request_logging import bind_request_context

security_scheme = HTTPBearer(auto_error=False)

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
    """Hash a plaintext password with bcrypt at the configured cost. Plaintext must not be logged."""
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
    sub: str, email: str | None = None, uid: str | None = None
) -> str:
    """Create a short-lived HS256 access JWT with ``sub``, optional ``email``/``uid``, ``iat``, ``exp``.

    ``sub`` is the durable human identity (the email). ``uid`` is the ``user.id`` (a UUID) and is
    kept as a separate claim because audit attribution stores it in ``audit_event.actor_id``, which
    is a foreign key to ``user.id`` — the email in ``sub`` cannot satisfy that constraint.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=settings.jwt_access_expire_minutes)
    payload: dict[str, Any] = {
        "sub": sub,
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    if email is not None:
        payload["email"] = email
    if uid is not None:
        payload["uid"] = uid
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


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


def create_password_reset_token(user_id: str, email: str) -> str:
    """Create a typed PASSWORD_RESET JWT for the reset-password link."""
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(hours=settings.password_reset_link_expire_hours)
    payload = {
        "sub": user_id,
        "email": email.lower(),
        "type": TokenType.PASSWORD_RESET.value,
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
    """Resolve the current user's token claims from a Bearer token or the access cookie.

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

    payload = decode_token(token)
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
    """Resolve token claims when present; return None when there are no credentials."""
    settings = get_settings()
    if not settings.auth_enabled:
        return {"sub": "anonymous", "scope": AuthScope.READ.value}
    token = _token_from_request(request, credentials)
    if not token:
        return None
    payload = decode_token(token)
    if payload is not None:
        _bind_actor(payload)
    return payload
