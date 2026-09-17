"""Auth routes: signup/activation plus the staff sign-in and session lifecycle (Issue 16).

Sign-in is by email OTP or password (rate-limited per account and per IP). Browser clients receive
an httpOnly access cookie (15 minutes), an httpOnly refresh cookie and a readable CSRF cookie, all
``SameSite=Lax`` and ``Secure`` outside development; API clients use ``Authorization: Bearer``.

**A session is a token family.** A sign-in starts one: its first refresh row and every row rotated
from it share ``family_id``. ``/auth/refresh`` rotates (one-time use). Presenting a token that was
already rotated is a replay, and revokes the **whole family**, because the server cannot tell
whether the thief or the owner holds the newest token; the user's other sessions are untouched. The
one exception is a replay within ``REFRESH_REUSE_GRACE_SECONDS`` of the rotation, which is two tabs
whose refresh requests crossed: it gets a fresh access token and nothing else. Sign-out revokes the
family server-side; the sessions list shows one entry per family, and revoking one revokes its
family. The CSRF token is bound to the family (``src.core.csrf_middleware``).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.commons.enums import SecurityAuditEvent, SecurityAuditOutcome, TokenType
from src.commons.exceptions import InvalidImageError
from src.commons.ids import new_id
from src.core.client_ip import client_ip_or_unknown, resolve_client_ip
from src.core.config import Settings, get_settings
from src.core.csrf_middleware import csrf_token_is_bound, mint_csrf_token
from src.core.email_send import (
    EmailDeliveryError,
    send_activation_email,
    send_email_change_verification_email,
    send_otp_email,
    send_password_reset_email,
)
from src.core.otp_store import (
    check_rate_limit_email,
    check_rate_limit_ip,
    invalidate_otp,
    record_rate_limit_email,
    record_rate_limit_ip,
    store_otp,
    verify_otp,
)
from src.core.rate_limit import password_login_limiter
from src.core.rbac import resource_permissions_for_user
from src.core.refresh_token_policy import (
    enforce_refresh_row_absolute_max,
    enforce_server_idle_timeout,
    family_is_live,
    get_valid_refresh_token_row,
    in_family,
    revoke_families_except,
    revoke_family,
)
from src.core.security import (
    CurrentStaff,
    create_activation_token,
    create_email_change_token,
    create_password_reset_token,
    decode_activation_token,
    decode_email_change_token,
    decode_password_reset_token,
    get_current_user,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    password_fingerprint,
    resolve_active_user,
    verify_password,
)
from src.core.verification import enforce_verification_resend_cooldown
from src.database.models import RefreshToken, User
from src.database.session import get_db
from src.modules.account.avatars import (
    AVATAR_CONTENT_TYPE,
    LocalAvatarStorage,
    is_valid_avatar_token,
    new_avatar_token,
    process_avatar,
    sniff_image_type,
)
from src.schemas.auth import (
    AuthConfigResponse,
    EmailChangeIn,
    EmailChangeRequestOut,
    MeResponse,
    OTPRequestIn,
    OTPRequestOut,
    OTPVerifyIn,
    PasswordChangeIn,
    PasswordChangeOut,
    PasswordForgotIn,
    PasswordForgotOut,
    PasswordLoginIn,
    PasswordResetIn,
    PasswordResetOut,
    ProfileUpdateIn,
    ResendVerificationOut,
    SessionListOut,
    SessionOut,
    SignupRequestIn,
    SignupResponseOut,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[dict, Depends(get_current_user)]

# Read uploads in bounded chunks so an oversized body is rejected without ever holding the
# whole file in memory (mirrors the maintenance/document upload paths).
_UPLOAD_CHUNK_BYTES = 64 * 1024


# --------------------------------------------------------------------------------------
# Helpers: request metadata, refresh-token lifecycle, and session cookies
# --------------------------------------------------------------------------------------


def _user_agent_for_refresh_session(request: Request) -> str | None:
    """Return the User-Agent for the refresh row (max 512 chars), or None if empty."""
    raw = (request.headers.get("User-Agent") or "")[:512]
    return raw.strip() or None


def _as_utc_aware(dt: datetime) -> datetime:
    """Normalize a DB ``datetime`` to UTC-aware for comparisons (SQLite returns naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _create_refresh_token_for_user(
    db: Session,
    user_id: str,
    settings: Settings,
    *,
    user_agent: str | None = None,
    sign_in_ip: str | None = None,
) -> tuple[str, str]:
    """Start a session: store a new family's first refresh row; return ``(raw token, family id)``.

    The family id is the first row's own id, so a session keeps one stable id however often it
    rotates. Only the hash of the raw token is stored.
    """
    raw = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    row_id = new_id()
    rt = RefreshToken(
        id=row_id,
        family_id=row_id,
        user_id=user_id,
        token_hash=hash_refresh_token(raw),
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        user_agent=user_agent,
        sign_in_ip=sign_in_ip,
        last_seen_at=now,
        session_started_at=now,
    )
    db.add(rt)
    db.commit()
    return raw, row_id


def _get_refresh_token_row_by_raw(
    db: Session, raw_token: str | None
) -> RefreshToken | None:
    """Return the refresh row for this cookie value in any state, or None if missing."""
    if not raw_token or not raw_token.strip():
        return None
    token_hash = hash_refresh_token(raw_token.strip())
    return db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()


def _revoke_all_refresh_tokens_for_user(db: Session, user_id: str) -> None:
    """Revoke every still-active refresh token for the user (a password reset)."""
    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


def _rotate_refresh_token_for_session(
    db: Session,
    old_row: RefreshToken,
    settings: Settings,
    *,
    user_agent: str | None,
    sign_in_ip: str | None,
) -> str:
    """Spend the presented row and add its successor to the same family; return the new raw token.

    The spent row is marked ``rotated_at`` (and revoked): presenting it again is a replay.
    """
    now = datetime.now(UTC)
    old_row.revoked_at = now
    old_row.rotated_at = now
    raw = secrets.token_urlsafe(32)
    rt = RefreshToken(
        user_id=old_row.user_id,
        family_id=old_row.session_id,
        token_hash=hash_refresh_token(raw),
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        user_agent=user_agent,
        sign_in_ip=sign_in_ip,
        last_seen_at=now,
        session_started_at=old_row.session_started_at or now,
    )
    db.add(rt)
    db.commit()
    return raw


def _current_session_id(
    db: Session, request: Request, settings: Settings, claims: dict | None = None
) -> str | None:
    """The caller's own session: its refresh cookie's family, else the access token's ``sid``."""
    row = get_valid_refresh_token_row(
        db, request.cookies.get(settings.refresh_token_cookie_name)
    )
    if row is not None:
        return row.session_id
    sid = (claims or {}).get("sid")
    return str(sid) if sid else None


def _refresh_csrf_ok(request: Request, settings: Settings, family_id: str) -> bool:
    """For a refresh or sign-out, whether the CSRF token (when there is one) is this session's.

    The middleware has already checked that the echoed token matches the cookie; a request carrying
    only the refresh cookie has no access token to bind against, so the binding is checked here,
    against the refresh token's own family. A browser signed in before Issue 16 holds no CSRF
    cookie (it expired with the access cookie) or an unsigned one (no ``.``); either is let through
    once, and this response mints a bound one. ``SameSite=Lax`` and the Fetch Metadata check still
    stop a cross-site request, and a refresh only ever rotates the caller's own session.
    """
    token = request.cookies.get(settings.csrf_cookie_name)
    if token is None or "." not in token:
        return True
    return csrf_token_is_bound(token, family_id)


def _audit_refresh_reuse(user_id: str, family_id: str, revoked: int) -> None:
    """Record a replayed refresh token: a security event, keyed on by log aggregators."""
    logger.warning(
        "SECURITY_AUDIT %s outcome=%s user_id=%s session=%s revoked_rows=%d",
        SecurityAuditEvent.REFRESH_TOKEN_REUSE.value,
        SecurityAuditOutcome.FAILURE.value,
        user_id,
        family_id,
        revoked,
    )


def _set_refresh_token_cookie(
    response: Response, token: str, settings: Settings
) -> None:
    """Set the httpOnly, SameSite=Lax refresh token cookie (Secure per SESSION_COOKIE_SECURE)."""
    response.set_cookie(
        key=settings.refresh_token_cookie_name,
        value=token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        secure=settings.session_cookies_secure,
        samesite="lax",
        path="/",
    )


def _clear_refresh_token_cookie(response: Response, settings: Settings) -> None:
    """Clear the refresh token cookie."""
    response.delete_cookie(
        key=settings.refresh_token_cookie_name, path="/", samesite="lax"
    )


def _set_access_token_cookie(
    response: Response, token: str, settings: Settings
) -> None:
    """Set the httpOnly JWT access cookie (max_age aligned with JWT expiry)."""
    response.set_cookie(
        key=settings.access_token_cookie_name,
        value=token,
        max_age=settings.jwt_access_expire_minutes * 60,
        httponly=True,
        secure=settings.session_cookies_secure,
        samesite="lax",
        path="/",
    )


def _clear_access_token_cookie(response: Response, settings: Settings) -> None:
    """Clear the httpOnly access token cookie."""
    response.delete_cookie(
        key=settings.access_token_cookie_name, path="/", samesite="lax"
    )


def _set_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    """Set the readable CSRF cookie (session-bound, signed double-submit).

    It lives as long as the refresh cookie, not the access cookie: a browser whose access cookie
    has expired still holds the token its silent refresh must echo.
    """
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=False,
        secure=settings.session_cookies_secure,
        samesite="lax",
        path="/",
    )


def _clear_csrf_cookie(response: Response, settings: Settings) -> None:
    """Clear the CSRF double-submit cookie."""
    response.delete_cookie(key=settings.csrf_cookie_name, path="/", samesite="lax")


def _set_auth_session_cookies(
    response: Response, access_token: str, settings: Settings, session_id: str
) -> None:
    """Issue a fresh access JWT (httpOnly) and a CSRF token bound to ``session_id`` (readable)."""
    _set_access_token_cookie(response, access_token, settings)
    _set_csrf_cookie(response, mint_csrf_token(session_id), settings)


def _refresh_failed_response(settings: Settings) -> JSONResponse:
    """Return a 401 for a failed refresh with all session cookies cleared."""
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid or expired refresh token. Sign in again."},
    )
    _clear_refresh_token_cookie(response, settings)
    _clear_access_token_cookie(response, settings)
    _clear_csrf_cookie(response, settings)
    return response


def _login_response(
    db: Session, request: Request, user: User, settings: Settings
) -> JSONResponse:
    """Record the sign-in and build a token response with session cookies set."""
    sign_ip = resolve_client_ip(request)
    user.last_login = datetime.now(UTC)
    db.commit()
    refresh_token, session_id = _create_refresh_token_for_user(
        db,
        str(user.id),
        settings,
        user_agent=_user_agent_for_refresh_session(request),
        sign_in_ip=sign_ip,
    )
    access_token = issue_access_token(db, user, sid=session_id)
    data = TokenResponse(
        access_token="",
        token_type=TokenType.BEARER.value,
        email=user.email,
    )
    response = JSONResponse(content=data.model_dump())
    _set_refresh_token_cookie(response, refresh_token, settings)
    _set_auth_session_cookies(response, access_token, settings, session_id)
    return response


def _get_current_user_by_token(db: Session, current_user: dict) -> User:
    """Resolve the current account from JWT claims; 401 if unknown, deactivated or deleted.

    :func:`~src.core.security.resolve_active_user`, the identity path every authenticated route
    shares (Issue 15).
    """
    return resolve_active_user(db, current_user)


def _avatar_url_for(user: User) -> str | None:
    """Return the URL the shell should render for ``user``'s avatar, or ``None``.

    An **uploaded** picture wins over a pasted external URL (Issue #130), so there is exactly one
    answer to "what is this user's avatar" even when both columns are set. The uploaded URL is
    built from the opaque token alone — it carries no user id or email — and changes on every
    upload, so a replaced picture cannot keep being served from a cached URL.
    """
    if user.avatar_key:
        return f"/api/v1/auth/avatars/{user.avatar_key}"
    return user.avatar_url


def _user_to_me_response(
    user: User, permissions: dict[str, str] | None = None
) -> MeResponse:
    """Build the public profile response from a User row."""
    return MeResponse(
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        date_of_birth=user.date_of_birth,
        avatar_url=_avatar_url_for(user),
        auth_provider=user.auth_provider,
        is_verified=user.is_verified,
        last_login=user.last_login,
        created_at=user.created_at,
        has_password=user.password is not None,
        role=user.role,
        permissions=permissions or {},
    )


# --------------------------------------------------------------------------------------
# Public config
# --------------------------------------------------------------------------------------


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config(settings: SettingsDep) -> AuthConfigResponse:
    """Return public auth feature flags for the frontend (no auth required)."""
    return AuthConfigResponse(
        signup_enabled=settings.signup_enabled,
        otp_login_enabled=settings.auth_otp_login_enabled,
        password_login_enabled=settings.auth_password_login_enabled,
    )


# --------------------------------------------------------------------------------------
# Signup + activation
# --------------------------------------------------------------------------------------


@router.post("/signup", response_model=SignupResponseOut)
async def signup(
    request: Request,
    body: SignupRequestIn,
    db: DbSession,
    settings: SettingsDep,
) -> SignupResponseOut:
    """Register a new user by email and send an activation link.

    Gated by ``SIGNUP_ENABLED``. Always returns the same generic message whether the
    email is new or already registered (no account enumeration).
    """
    if not settings.signup_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signup is not enabled.",
        )

    email = body.email.lower().strip()
    generic = SignupResponseOut()

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        logger.info("Signup for already-registered email suppressed (no enumeration).")
        return generic

    user = User(email=email, is_verified=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    base_url = str(request.base_url).rstrip("/")
    token = create_activation_token(user_id=str(user.id), email=email)
    activation_link = f"{base_url}/api/v1/auth/activate?token={quote(token, safe='')}"
    try:
        # Blocking SMTP send — offload to a thread so it never stalls the event loop.
        await asyncio.to_thread(
            send_activation_email,
            to_email=email,
            activation_link=activation_link,
            expire_hours=settings.activation_link_expire_hours,
        )
    except EmailDeliveryError as exc:
        db.delete(user)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Could not send activation email. Try again later or contact support "
                "if the problem continues."
            ),
        ) from exc
    return generic


@router.get("/activate")
async def activate_account(
    request: Request,
    token: str,
    db: DbSession,
) -> RedirectResponse:
    """Activate an account from the signup email link.

    Validates the activation token, sets ``is_verified=True`` (idempotent), and redirects
    to the app with an ``activated`` status. Invalid/expired tokens redirect with
    ``activated=invalid``.
    """
    base = str(request.base_url).rstrip("/")

    def _redirect(result: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{base}/?activated={result}",
            status_code=status.HTTP_302_FOUND,
        )

    payload = decode_activation_token(token)
    if not payload:
        return _redirect("invalid")

    user_id = payload.get("sub")
    email = (payload.get("email") or "").lower()
    if not user_id or not email:
        return _redirect("invalid")

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None or user.email.lower() != email:
        return _redirect("invalid")

    db.execute(update(User).where(User.id == user_id).values(is_verified=True))
    db.commit()
    return _redirect("success")


# --------------------------------------------------------------------------------------
# OTP sign-in
# --------------------------------------------------------------------------------------


@router.post("/otp/request", response_model=OTPRequestOut)
async def request_otp(
    request: Request,
    body: OTPRequestIn,
    db: DbSession,
    settings: SettingsDep,
) -> OTPRequestOut:
    """Request an OTP for the given email.

    Only registered, activated emails receive a code. Rate-limited per email and per IP.
    A single generic 403 is returned for unknown/unactivated emails (no enumeration).
    """
    if not settings.auth_otp_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OTP sign-in is disabled.",
        )
    email = body.email.lower().strip()
    ip = client_ip_or_unknown(request)

    if not check_rate_limit_ip(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )
    if not check_rate_limit_email(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests for this email. Try again later.",
        )

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or user.is_deleted or not user.is_active or not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="If this email is registered and activated, you will receive a code.",
        )

    code = store_otp(email)
    try:
        # Blocking SMTP send — offload to a thread so it never stalls the event loop.
        await asyncio.to_thread(send_otp_email, email, code, client_ip=ip)
    except EmailDeliveryError as exc:
        invalidate_otp(email)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Could not send sign-in code. Try again later or contact support "
                "if the problem continues."
            ),
        ) from exc
    record_rate_limit_email(email)
    record_rate_limit_ip(ip)
    return OTPRequestOut()


@router.post("/otp/verify")
async def verify_otp_endpoint(
    request: Request,
    body: OTPVerifyIn,
    db: DbSession,
    settings: SettingsDep,
) -> JSONResponse:
    """Verify an OTP and, on success, issue an access JWT + refresh/CSRF cookies."""
    if not settings.auth_otp_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OTP sign-in is disabled.",
        )
    email = body.email.lower().strip()
    code = body.code.strip()

    if not verify_otp(email, code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired code. Request a new code.",
        )

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or user.is_deleted or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found. Complete signup and activation first.",
        )
    return _login_response(db, request, user, settings)


# --------------------------------------------------------------------------------------
# Password sign-in
# --------------------------------------------------------------------------------------


@router.post("/password/login")
async def password_login_endpoint(
    request: Request,
    body: PasswordLoginIn,
    db: DbSession,
    settings: SettingsDep,
) -> JSONResponse:
    """Sign in with email + password (fallback when OTP delivery is unavailable)."""
    if not settings.auth_password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password sign-in is disabled.",
        )
    email = body.email.lower().strip()
    ip = client_ip_or_unknown(request)
    window = float(settings.password_login_rate_limit_window_seconds)
    # Per-IP first (blunts password-spraying across many accounts), then per-email (blunts
    # brute-forcing one account). Both are recorded on every attempt so sustained abuse keeps
    # tripping the limit; a wrong-but-unthrottled guess still returns the generic 401 below.
    if not password_login_limiter.check_and_record(
        f"ip:{ip}",
        limit=settings.password_login_rate_limit_per_ip,
        window_seconds=window,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Try again later.",
        )
    if not password_login_limiter.check_and_record(
        f"email:{email}",
        limit=settings.password_login_rate_limit_per_email,
        window_seconds=window,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts for this account. Try again later.",
        )
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if (
        user is None
        or user.is_deleted
        or not user.is_active
        or not user.is_verified
        or user.password is None
        or not verify_password(body.password, user.password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    return _login_response(db, request, user, settings)


# --------------------------------------------------------------------------------------
# Password reset (forgot / reset)
# --------------------------------------------------------------------------------------


@router.post("/password/forgot", response_model=PasswordForgotOut)
async def password_forgot(
    request: Request,
    body: PasswordForgotIn,
    db: DbSession,
    settings: SettingsDep,
) -> PasswordForgotOut:
    """Start a password reset by email.

    Always returns the same generic message whether or not the email exists or is
    verified (no account enumeration). When the account is eligible, a signed,
    expiring reset link is emailed.
    """
    if not settings.auth_password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password sign-in is disabled.",
        )
    email = body.email.lower().strip()
    generic = PasswordForgotOut()

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or user.is_deleted or not user.is_verified:
        logger.info("Password reset requested for ineligible email (no enumeration).")
        return generic

    base_url = str(request.base_url).rstrip("/")
    token = create_password_reset_token(
        user_id=str(user.id), email=email, password_hash=user.password
    )
    reset_link = f"{base_url}/reset-password?token={quote(token, safe='')}"
    try:
        # Blocking SMTP send — offload to a thread so it never stalls the event loop.
        await asyncio.to_thread(
            send_password_reset_email,
            to_email=email,
            reset_link=reset_link,
            expire_hours=settings.password_reset_link_expire_hours,
        )
    except EmailDeliveryError:
        logger.warning("Password reset email delivery failed for a requesting user.")
        return generic
    return generic


@router.post("/password/reset", response_model=PasswordResetOut)
async def password_reset(
    body: PasswordResetIn,
    db: DbSession,
    settings: SettingsDep,
) -> PasswordResetOut:
    """Complete a password reset with a valid signed token and a new password.

    On success the password is updated and every existing refresh session for the user
    is revoked, forcing a fresh sign-in.
    """
    if not settings.auth_password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password sign-in is disabled.",
        )
    payload = decode_password_reset_token(body.token.strip())
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )
    user_id = str(payload.get("sub") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if (
        user is None
        or user.is_deleted
        or not user.is_active
        or user.email.lower().strip() != email
        # Single use: the link names the password it resets, and a used link no longer matches.
        or not hmac.compare_digest(
            str(payload.get("pwv") or ""), password_fingerprint(user.password)
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )
    user.password = hash_password(body.new_password)
    db.commit()
    _revoke_all_refresh_tokens_for_user(db, str(user.id))
    return PasswordResetOut()


# --------------------------------------------------------------------------------------
# Refresh rotation + logout
# --------------------------------------------------------------------------------------


@router.post("/refresh")
async def refresh_access_token(
    request: Request, db: DbSession, settings: SettingsDep
) -> JSONResponse:
    """Rotate the refresh token and issue a new access JWT.

    Each successful call spends the presented refresh token and sets its successor, in the same
    family (one-time use). A token that was already rotated is a replay:

    * within ``REFRESH_REUSE_GRACE_SECONDS`` of its rotation, while its family is still signed in,
      it is two tabs whose refreshes crossed: a fresh access token, no new refresh token, nothing
      revoked;
    * otherwise every token in its family is revoked and the client must sign in again. The
      user's other sessions are left alone.

    Returns 401 (with every session cookie cleared) when the token is missing, unknown, revoked,
    expired, past the idle or absolute cap, replayed, or its account is switched off.
    """
    raw = request.cookies.get(settings.refresh_token_cookie_name)
    if not raw or not raw.strip():
        return _refresh_failed_response(settings)

    row = _get_refresh_token_row_by_raw(db, raw)
    if row is None:
        return _refresh_failed_response(settings)
    family_id = row.session_id
    if not _refresh_csrf_ok(request, settings, family_id):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Invalid or missing CSRF token"},
        )

    now = datetime.now(UTC)
    if row.rotated_at is not None:
        within_grace = now - _as_utc_aware(row.rotated_at) <= timedelta(
            seconds=settings.refresh_reuse_grace_seconds
        )
        if within_grace and family_is_live(db, family_id):
            user = db.execute(
                select(User).where(User.id == row.user_id)
            ).scalar_one_or_none()
            if user is None or not user.is_active or user.is_deleted:
                return _refresh_failed_response(settings)
            response = JSONResponse(
                content=TokenResponse(
                    access_token="", token_type=TokenType.BEARER.value, email=user.email
                ).model_dump()
            )
            # No refresh cookie: the request that rotated it has already set the successor.
            _set_auth_session_cookies(
                response,
                issue_access_token(db, user, sid=family_id),
                settings,
                family_id,
            )
            return response
        _audit_refresh_reuse(
            row.user_id, family_id, revoke_family(db, family_id, now=now)
        )
        return _refresh_failed_response(settings)
    if row.revoked_at is not None:
        # Signed out, revoked from another device, or timed out: not a replay, just over.
        return _refresh_failed_response(settings)

    if _as_utc_aware(row.expires_at) <= now:
        return _refresh_failed_response(settings)
    if not enforce_refresh_row_absolute_max(db, row):
        return _refresh_failed_response(settings)
    if not enforce_server_idle_timeout(db, row):
        return _refresh_failed_response(settings)

    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if user is None or not user.is_active or user.is_deleted:
        revoke_family(db, family_id, now=now)
        return _refresh_failed_response(settings)

    ua = _user_agent_for_refresh_session(request) or row.user_agent
    sign_ip = resolve_client_ip(request) or row.sign_in_ip
    new_raw = _rotate_refresh_token_for_session(
        db, row, settings, user_agent=ua, sign_in_ip=sign_ip
    )
    access_token = issue_access_token(db, user, sid=family_id)
    data = TokenResponse(
        access_token="",
        token_type=TokenType.BEARER.value,
        email=user.email,
    )
    response = JSONResponse(content=data.model_dump())
    _set_refresh_token_cookie(response, new_raw, settings)
    _set_auth_session_cookies(response, access_token, settings, family_id)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, db: DbSession, settings: SettingsDep) -> Response:
    """Sign out: revoke this session's whole token family server-side and clear every cookie.

    The family, not just the presented row, so a sign-out cannot be undone by a copy of an earlier
    token from the same session.
    """
    row = _get_refresh_token_row_by_raw(
        db, request.cookies.get(settings.refresh_token_cookie_name)
    )
    if row is not None:
        if not _refresh_csrf_ok(request, settings, row.session_id):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid or missing CSRF token"},
            )
        revoke_family(db, row.session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_token_cookie(response, settings)
    _clear_access_token_cookie(response, settings)
    _clear_csrf_cookie(response, settings)
    return response


# --------------------------------------------------------------------------------------
# Current user + sessions
# --------------------------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
def get_me(db: DbSession, user: CurrentStaff) -> MeResponse:
    """Return the current user's profile and effective RBAC permissions.

    Requires a valid access token (Bearer or cookie) for an active account
    (:func:`~src.core.security.get_current_staff`). ``permissions`` maps each resource the caller's
    role can reach to its effective maximum verb (after parent-resource cascade).
    """
    # Union of the user's active, unscoped role assignments (Issue #136); falls back to the
    # ``User.role`` mirror for a user with no assignment rows.
    permissions = resource_permissions_for_user(db, user)
    return _user_to_me_response(user, permissions)


@router.patch("/me", response_model=MeResponse)
async def patch_me(
    db: DbSession,
    current_user: CurrentUser,
    body: ProfileUpdateIn,
) -> MeResponse:
    """Update the current user's profile (only provided fields). Requires a valid token."""
    user = _get_current_user_by_token(db, current_user)
    payload = body.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return _user_to_me_response(user)


# --------------------------------------------------------------------------------------
# Profile picture (Issue #130) — upload, replace, remove and serve.
#
# Authorisation is structural rather than a check: every write acts on the row resolved from the
# caller's own token, and there is no user parameter anywhere, so a user cannot reach anyone
# else's picture. Uploads are content-sniffed (the declared type is a hint, the magic bytes are
# the evidence), size-capped before the body is buffered, and re-encoded by us to a square WEBP —
# so the stored bytes carry no EXIF (and therefore no GPS coordinates of the user's home) and are
# never the client's original file. Serving is by opaque token, which is what lets the URL be
# stable and cacheable without being enumerable.
# --------------------------------------------------------------------------------------


async def _read_avatar_within_cap(
    file: UploadFile, cap_bytes: int, declared_length: int | None
) -> bytes:
    """Read an upload into memory only if it stays within ``cap_bytes``, else raise 413.

    A declared ``Content-Length`` over the cap is rejected up front — before the body is read at
    all. The body is then read in bounded chunks and aborted the moment the running total exceeds
    the cap, so an under-declared (or unlabelled) oversized upload is still never fully buffered.
    """
    if declared_length is not None and declared_length > cap_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Profile picture exceeds the maximum size of {cap_bytes} bytes.",
        )
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > cap_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Profile picture exceeds the maximum size of {cap_bytes} bytes.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _require_avatar_image(data: bytes, declared_type: str | None) -> None:
    """Reject anything that is not an accepted image, by signature *and* declared type (415).

    Both must agree. The declared ``Content-Type`` comes from an untrusted client, so it is never
    trusted alone; the magic bytes are the evidence. Requiring them to match as well means a
    caller cannot mislabel a real image to steer later handling either.
    """
    sniffed = sniff_image_type(data)
    if sniffed is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Profile picture must be a JPEG, PNG or WebP image.",
        )
    if declared_type and declared_type.split(";")[0].strip().lower() != sniffed.value:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The uploaded file's content type does not match its contents.",
        )


def _replace_stored_avatar(
    db: Session, user: User, storage: LocalAvatarStorage, *, data: bytes | None
) -> None:
    """Point ``user`` at a newly stored avatar (or none), deleting whatever it replaces.

    The order matters. The new object is written **first**, then the row is committed, and only
    then is the old object deleted: if anything fails in between, the user still has a picture
    that serves. Deleting the old object last (and tolerating its absence) means the worst case is
    an orphaned file, never a broken avatar.
    """
    previous = user.avatar_key
    if data is None:
        user.avatar_key = None
    else:
        token = new_avatar_token()
        storage.save(token, data)
        user.avatar_key = token
        # An uploaded picture is now the answer, so the pasted URL is cleared rather than left
        # behind to reappear if the upload is later removed.
        user.avatar_url = None
    db.commit()
    db.refresh(user)
    if previous and previous != user.avatar_key:
        storage.delete(previous)


@router.post("/me/avatar", response_model=MeResponse)
async def upload_my_avatar(
    db: DbSession,
    settings: SettingsDep,
    current_user: CurrentUser,
    file: Annotated[
        UploadFile, File(description="Profile picture (JPEG, PNG or WebP).")
    ],
    content_length: Annotated[int | None, Header()] = None,
) -> MeResponse:
    """Upload (or replace) the signed-in user's profile picture.

    Only the caller's own picture can be set: the row comes from their token and there is no user
    parameter to point elsewhere. The upload must be a JPEG, PNG or WebP by **content sniff** as
    well as declared type (415) and within the configured cap, checked from the declared
    ``Content-Length`` before the body is buffered and again while reading (413). It is then
    re-encoded to a square WEBP with the EXIF stripped — so nothing of the original file, its
    metadata or any appended payload survives — and stored under a fresh opaque token, which
    retires the previous URL. A file that sniffs as an image but cannot be decoded is a 422.

    Returns the full profile, so the caller refreshes the shell avatar from one response.
    """
    user = _get_current_user_by_token(db, current_user)
    data = await _read_avatar_within_cap(
        file, settings.avatar_max_bytes, content_length
    )
    _require_avatar_image(data, file.content_type)
    try:
        # CPU-bound decode/resize/encode — off the event loop so a large photo never stalls it.
        processed = await asyncio.to_thread(
            process_avatar, data, size_px=settings.avatar_max_dimension_px
        )
    except InvalidImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    _replace_stored_avatar(
        db, user, LocalAvatarStorage(settings.avatar_storage_dir), data=processed
    )
    return _user_to_me_response(user)


@router.delete("/me/avatar", response_model=MeResponse)
async def remove_my_avatar(
    db: DbSession, settings: SettingsDep, current_user: CurrentUser
) -> MeResponse:
    """Remove the signed-in user's uploaded profile picture, reverting to their initial.

    Idempotent: removing when there is nothing to remove succeeds and changes nothing. The stored
    object is deleted along with the row's token, so the old URL stops serving immediately.
    """
    user = _get_current_user_by_token(db, current_user)
    _replace_stored_avatar(
        db, user, LocalAvatarStorage(settings.avatar_storage_dir), data=None
    )
    return _user_to_me_response(user)


@router.get("/avatars/{avatar_token}", name="serve_avatar")
async def serve_avatar(settings: SettingsDep, avatar_token: str) -> Response:
    """Serve one stored profile picture by its opaque token.

    Deliberately session-free, like the document-download endpoint: an ``<img>`` tag cannot carry
    an ``Authorization`` header, and the picture is the least sensitive thing an account has. What
    protects it is that the token is random, is not derived from the user, and is re-issued on
    every upload — so a URL is unguessable, discloses nothing about whose picture it is, and stops
    working the moment the picture is replaced or removed.

    That last property is also what makes the response cacheable: a new picture is always a new
    URL, so a cached one can never be stale. A malformed token and a missing object are both a
    plain 404, revealing nothing about which.
    """
    if not is_valid_avatar_token(avatar_token):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        )
    try:
        data = LocalAvatarStorage(settings.avatar_storage_dir).read(avatar_token)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found."
        ) from exc
    return Response(
        content=data,
        media_type=AVATAR_CONTENT_TYPE,
        headers={
            # ``private`` keeps it out of shared proxy caches: it is one person's photograph,
            # even though the URL itself is unguessable.
            "Cache-Control": f"private, max-age={settings.avatar_cache_max_age_seconds}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/me/password", response_model=PasswordChangeOut)
async def change_my_password(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    settings: SettingsDep,
    body: PasswordChangeIn,
) -> PasswordChangeOut:
    """Change the signed-in user's password after re-proving the current one.

    A sensitive action: it requires the account's current password (a wrong one, or an account
    that has never set a password, is a 400) and, on success, revokes every *other* refresh
    session so a leaked cookie elsewhere stops working — the device making the change keeps its
    session. Rejects reusing the same password (400).
    """
    user = _get_current_user_by_token(db, current_user)
    if user.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No password is set on this account yet. Use the password reset link to set one."
            ),
        )
    if not verify_password(body.current_password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if verify_password(body.new_password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current one.",
        )
    user.password = hash_password(body.new_password)
    db.commit()

    revoke_families_except(
        db, str(user.id), _current_session_id(db, request, settings, current_user)
    )
    return PasswordChangeOut()


@router.post("/me/email", response_model=EmailChangeRequestOut)
async def request_email_change(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    settings: SettingsDep,
    body: EmailChangeIn,
) -> EmailChangeRequestOut:
    """Start a re-verified change of the signed-in user's email address.

    Requires the current password (a sensitive action). The account's address is **not** changed
    here: a signed, expiring confirmation link is emailed to the *new* address, and the change
    only takes effect once that link is confirmed (``GET /auth/email/change/confirm``). Rejects a
    new address equal to the current one (400) or already used by another account (409).
    """
    user = _get_current_user_by_token(db, current_user)
    if user.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Set a password on your account before changing your email address."
            ),
        )
    if not verify_password(body.current_password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    new_email = body.new_email.lower().strip()
    if new_email == user.email.lower().strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That is already your email address.",
        )
    taken = db.execute(select(User).where(User.email == new_email)).scalar_one_or_none()
    if taken is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already in use.",
        )

    base_url = str(request.base_url).rstrip("/")
    token = create_email_change_token(user_id=str(user.id), new_email=new_email)
    confirm_link = (
        f"{base_url}/api/v1/auth/email/change/confirm?token={quote(token, safe='')}"
    )
    try:
        # Blocking SMTP send — offload to a thread so it never stalls the event loop.
        await asyncio.to_thread(
            send_email_change_verification_email,
            to_email=new_email,
            confirm_link=confirm_link,
            expire_hours=settings.email_change_link_expire_hours,
        )
    except EmailDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send the confirmation email. Try again later.",
        ) from exc
    return EmailChangeRequestOut()


@router.get("/email/change/confirm")
async def confirm_email_change(
    request: Request,
    token: str,
    db: DbSession,
) -> RedirectResponse:
    """Confirm an email change from the link sent to the new address, then apply it.

    Validates the signed EMAIL_CHANGE token, and — only if the new address is still free —
    moves the account to it, keeping the verified flag (the user just proved control of the new
    address). Redirects to the profile page with an ``emailChanged`` status; invalid, expired or
    now-conflicting links redirect with ``emailChanged=invalid``.
    """
    base = str(request.base_url).rstrip("/")

    def _redirect(result: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{base}/account/profile?emailChanged={result}",
            status_code=status.HTTP_302_FOUND,
        )

    payload = decode_email_change_token(token)
    if payload is None:
        return _redirect("invalid")
    user_id = str(payload.get("sub") or "").strip()
    new_email = str(payload.get("new_email") or "").strip().lower()
    if not user_id or not new_email:
        return _redirect("invalid")

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None or user.is_deleted:
        return _redirect("invalid")
    if user.email.lower().strip() == new_email:
        # Already applied (link opened twice): idempotent success.
        return _redirect("success")
    taken = db.execute(select(User).where(User.email == new_email)).scalar_one_or_none()
    if taken is not None:
        return _redirect("invalid")

    user.email = new_email
    user.is_verified = True
    db.commit()
    return _redirect("success")


@router.post("/me/resend-verification", response_model=ResendVerificationOut)
async def resend_verification_email(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    settings: SettingsDep,
) -> ResendVerificationOut:
    """Resend the account activation/verification email for the current user.

    Rejected (400) when the email is already verified and rate-limited (429) by a
    per-user cooldown based on ``last_verification_email_sent_at``.
    """
    user = _get_current_user_by_token(db, current_user)
    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified.",
        )
    enforce_verification_resend_cooldown(user, settings)

    email = user.email.lower().strip()
    base_url = str(request.base_url).rstrip("/")
    token = create_activation_token(user_id=str(user.id), email=email)
    activation_link = f"{base_url}/api/v1/auth/activate?token={quote(token, safe='')}"
    try:
        # Blocking SMTP send — offload to a thread so it never stalls the event loop.
        await asyncio.to_thread(
            send_activation_email,
            to_email=email,
            activation_link=activation_link,
            expire_hours=settings.activation_link_expire_hours,
        )
    except EmailDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send verification email. Try again later.",
        ) from exc
    user.last_verification_email_sent_at = datetime.now(UTC)
    db.commit()
    return ResendVerificationOut()


@router.get("/me/sessions", response_model=SessionListOut)
async def list_my_sessions(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    settings: SettingsDep,
) -> SessionListOut:
    """List the caller's signed-in sessions, one per token family; their own is ``is_current``.

    ``id`` is the family id, which stays the same however often the session's token rotates, so a
    revoke issued from this list always reaches the session it names.
    """
    user = _get_current_user_by_token(db, current_user)
    current = _current_session_id(db, request, settings, current_user)
    now = datetime.now(UTC)
    rows = (
        db.execute(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.last_seen_at.desc(), RefreshToken.expires_at.desc())
        )
        .scalars()
        .all()
    )
    sessions: dict[str, SessionOut] = {}
    for r in rows:
        # One live row per family in steady state; keep the most recently seen if a race left two.
        sessions.setdefault(
            r.session_id,
            SessionOut(
                id=r.session_id,
                expires_at=r.expires_at,
                is_current=r.session_id == current,
                user_agent=r.user_agent,
                sign_in_ip=r.sign_in_ip,
                last_seen_at=r.last_seen_at,
            ),
        )
    return SessionListOut(sessions=list(sessions.values()))


@router.delete("/me/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    settings: SettingsDep,
) -> Response:
    """Sign out everywhere else: revoke every session of the caller's except the current one."""
    user = _get_current_user_by_token(db, current_user)
    current = _current_session_id(db, request, settings, current_user)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active session cookie; cannot identify the current session.",
        )
    revoke_families_except(db, str(user.id), current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_one_session(
    request: Request,
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
    settings: SettingsDep,
) -> Response:
    """Revoke one of the caller's sessions (its whole family) by the id the list shows.

    Another user's session, or an unknown id, is 404 (never 403: a session id says nothing about
    whether it exists). The current session is signed out with ``POST /auth/logout`` instead.
    """
    user = _get_current_user_by_token(db, current_user)
    owned = db.execute(
        select(RefreshToken.id)
        .where(in_family(session_id), RefreshToken.user_id == user.id)
        .limit(1)
    ).first()
    if owned is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )
    if session_id == _current_session_id(db, request, settings, current_user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke the current session; use POST /auth/logout instead.",
        )
    revoke_family(db, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
