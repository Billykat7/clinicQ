"""One-time codes: one store for every kind of subject, email or phone (Issues 3, 17).

A code proves control of an address or a number. The staff email sign-in and the patient phone
sign-in (Issue 17) use this one implementation, keyed by ``(kind, identifier)``
(:class:`~src.commons.enums.OtpSubjectKind`), so the two cannot drift apart. Each code:

* is 6 digits by default (``OTP_LENGTH``) from a CSPRNG;
* is **stored only as a keyed hash**: the process holds ``HMAC(secret, kind, identifier, code)``,
  never the code, so a memory dump or a debugger shows nothing a person could type;
* **expires** after ``OTP_TTL_MINUTES`` and is **single-use**;
* **locks** after ``OTP_MAX_VERIFY_ATTEMPTS`` wrong guesses: from then on even the right code is
  refused until a new one is requested, which the request limits bound (so a six-digit code
  cannot be brute-forced by parallel guessing);
* can be **re-sent** only after ``OTP_RESEND_COOLDOWN_SECONDS``.

The request **rate limits** (per subject, per IP) live on the shared limiter
(:data:`src.core.rate_limit.otp_request_limiter`, Issue #179), so they hold across workers.

**The codes themselves are process-local**, as before: the image runs one worker, codes live
minutes, and a restart only means requesting a new code. A deployment that runs more than one
worker needs this store moved to Redis first (a known limit, recorded in the v0.3.0 release note).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from threading import Lock

from src.commons.enums import OtpSubjectKind, OtpVerification
from src.core.config import get_settings
from src.core.rate_limit import otp_request_limiter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Entry:
    """One issued code: its keyed hash, lifetime, and how it has been used."""

    digest: str
    issued_at: float
    expires_at: float
    attempts: int = 0
    used: bool = False


# {"<kind>:<identifier>": entry}
_otp_store: dict[str, _Entry] = {}
_store_lock = Lock()


def _key(kind: OtpSubjectKind, identifier: str) -> str:
    """The store key: the kind and the (lower-cased) identifier."""
    return f"{kind.value}:{identifier.strip().lower()}"


def _limit_key(kind: OtpSubjectKind, identifier: str) -> str:
    """The rate-limit bucket for a subject: ``otp:<kind>:<identifier>`` (as before Issue 17)."""
    return f"otp:{_key(kind, identifier)}"


def _digest(key: str, code: str) -> str:
    """HMAC-SHA256 of the code, bound to its subject, under a key derived from the app secret."""
    secret = hashlib.sha256(
        b"clinicq-otp-v1|" + get_settings().jwt_secret.encode()
    ).digest()
    return hmac.new(secret, f"{key}|{code}".encode(), hashlib.sha256).hexdigest()


def _window_seconds() -> float:
    """The OTP rate-limit window, in seconds (configured in minutes)."""
    return get_settings().otp_rate_limit_window_minutes * 60


# --------------------------------------------------------------------------------------
# Issue and verify, for any subject
# --------------------------------------------------------------------------------------


def generate_otp() -> str:
    """Generate a numeric OTP of the configured length using a CSPRNG."""
    length = get_settings().otp_length
    return "".join(secrets.choice("0123456789") for _ in range(length))


def resend_wait_seconds(kind: OtpSubjectKind, identifier: str) -> int:
    """Seconds until a new code may be issued for this subject (0: now).

    A code issued less than ``OTP_RESEND_COOLDOWN_SECONDS`` ago blocks a new one, so a
    button-mash, or a script, cannot flood a phone with texts.
    """
    cooldown = get_settings().otp_resend_cooldown_seconds
    with _store_lock:
        entry = _otp_store.get(_key(kind, identifier))
    if entry is None or entry.used:
        return 0
    return max(0, int(entry.issued_at + cooldown - time.time() + 0.999))


def issue_code(kind: OtpSubjectKind, identifier: str) -> str:
    """Issue a new code for the subject, replacing any earlier one, and return it to be sent.

    The return value is the only place the code exists in the clear: the caller hands it to the
    transport and drops it. Never log it.
    """
    code = generate_otp()
    key = _key(kind, identifier)
    now = time.time()
    with _store_lock:
        _otp_store[key] = _Entry(
            digest=_digest(key, code),
            issued_at=now,
            expires_at=now + get_settings().otp_ttl_minutes * 60,
        )
    return code


def check_code(kind: OtpSubjectKind, identifier: str, code: str) -> OtpVerification:
    """Check ``code`` for the subject; spend it on success, count it on failure.

    See :class:`~src.commons.enums.OtpVerification` for the outcomes. Constant-time comparison; a
    wrong guess on a locked, expired or used code does not extend anything.
    """
    key = _key(kind, identifier)
    max_attempts = get_settings().otp_max_verify_attempts
    with _store_lock:
        entry = _otp_store.get(key)
        if entry is None:
            return OtpVerification.INVALID
        if entry.used or time.time() > entry.expires_at:
            return OtpVerification.EXPIRED
        if entry.attempts >= max_attempts:
            return OtpVerification.LOCKED
        if hmac.compare_digest(entry.digest, _digest(key, code.strip())):
            entry.used = True
            return OtpVerification.VERIFIED
        entry.attempts += 1
        if entry.attempts >= max_attempts:
            # Logged by kind only: the identifier is personal data and the code is a secret.
            logger.warning(
                "One-time code locked after %d wrong attempts (%s)",
                max_attempts,
                kind.value,
            )
            return OtpVerification.LOCKED
        return OtpVerification.INVALID


def attempts_left(kind: OtpSubjectKind, identifier: str) -> int:
    """Wrong guesses the current code can still take before it locks (0 when there is none)."""
    with _store_lock:
        entry = _otp_store.get(_key(kind, identifier))
    if entry is None:
        return 0
    return max(0, get_settings().otp_max_verify_attempts - entry.attempts)


def discard_code(kind: OtpSubjectKind, identifier: str) -> None:
    """Forget the subject's code (e.g. after its delivery failed)."""
    with _store_lock:
        _otp_store.pop(_key(kind, identifier), None)


# --------------------------------------------------------------------------------------
# Request rate limits, on the shared limiter
# --------------------------------------------------------------------------------------


def within_request_limit(kind: OtpSubjectKind, identifier: str, ip: str) -> bool:
    """Whether both the subject and the IP are still under their OTP request budgets."""
    settings = get_settings()
    per_subject = (
        settings.otp_rate_limit_per_email
        if kind is OtpSubjectKind.EMAIL
        else settings.otp_rate_limit_per_phone
    )
    window = _window_seconds()
    return otp_request_limiter.within_limit(
        f"otp:ip:{ip}", limit=settings.otp_rate_limit_per_ip, window_seconds=window
    ) and otp_request_limiter.within_limit(
        _limit_key(kind, identifier), limit=per_subject, window_seconds=window
    )


def record_request(kind: OtpSubjectKind, identifier: str, ip: str) -> None:
    """Count one sent code against the subject's and the IP's budgets (after the send worked)."""
    window = _window_seconds()
    otp_request_limiter.record(_limit_key(kind, identifier), window_seconds=window)
    otp_request_limiter.record(f"otp:ip:{ip}", window_seconds=window)


# --------------------------------------------------------------------------------------
# The email sign-in's original names (Issue 3), now over the shared store
# --------------------------------------------------------------------------------------


def check_rate_limit_email(email: str) -> bool:
    """Return True if the email is still under its OTP rate limit (records nothing)."""
    return otp_request_limiter.within_limit(
        _limit_key(OtpSubjectKind.EMAIL, email),
        limit=get_settings().otp_rate_limit_per_email,
        window_seconds=_window_seconds(),
    )


def check_rate_limit_ip(ip: str) -> bool:
    """Return True if the IP is still under its OTP rate limit (records nothing)."""
    return otp_request_limiter.within_limit(
        f"otp:ip:{ip}",
        limit=get_settings().otp_rate_limit_per_ip,
        window_seconds=_window_seconds(),
    )


def record_rate_limit_email(email: str) -> None:
    """Record an OTP request for this email (called only after the mail is actually sent)."""
    otp_request_limiter.record(
        _limit_key(OtpSubjectKind.EMAIL, email), window_seconds=_window_seconds()
    )


def record_rate_limit_ip(ip: str) -> None:
    """Record an OTP request for this IP (called only after the mail is actually sent)."""
    otp_request_limiter.record(f"otp:ip:{ip}", window_seconds=_window_seconds())


def store_otp(email: str) -> str:
    """Issue an email sign-in code (overwrites any prior code) and return it to be mailed."""
    return issue_code(OtpSubjectKind.EMAIL, email)


def invalidate_otp(email: str) -> None:
    """Remove any stored email code (e.g. after failed email delivery)."""
    discard_code(OtpSubjectKind.EMAIL, email)


def verify_otp(email: str, code: str) -> bool:
    """Verify an email sign-in code: True only for the right, unexpired, unused, unlocked code."""
    return check_code(OtpSubjectKind.EMAIL, email, code) is OtpVerification.VERIFIED


def reset_otp_state() -> None:
    """Clear all OTP codes and OTP rate-limit state (test isolation helper)."""
    with _store_lock:
        _otp_store.clear()
    otp_request_limiter.reset()
