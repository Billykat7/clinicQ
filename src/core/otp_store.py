"""OTP store for email sign-in: codes in this process, rate limits on the shared backend.

Ported from the ``maps`` project. OTP codes are stored keyed by email with a short TTL and
single-use semantics.

**M30 (Issue #179)** moved the rate limiting out of this module. It used to keep two hand-rolled
timestamp dictionaries of its own — a third implementation of a sliding window, with its own
pruning, its own lock and its own drift — while the OTP per-IP budget was simultaneously keyed on
the reverse proxy's address (pen-test findings **F-02** and **F-04**). The windows now live on
:data:`src.core.rate_limit.otp_request_limiter`, so OTP shares one backend, one window semantic and
one cross-worker story with every other limiter in the app.

The **codes themselves** stay process-local. They are single-use, expire in minutes, and are
verified by the same worker pattern as before; moving them to a shared store is a different change
with a different risk profile (secrets at rest in a cache) and Issue #179 names it a non-goal.
"""

from __future__ import annotations

import logging
import secrets
import time
from threading import Lock
from typing import Any

from src.core.config import get_settings
from src.core.rate_limit import otp_request_limiter

logger = logging.getLogger(__name__)

# {email: {"code": str, "expires_at": float, "used": bool}}
_otp_store: dict[str, dict[str, Any]] = {}
_store_lock = Lock()


def _window_seconds() -> float:
    """The OTP rate-limit window, in seconds (configured in minutes)."""
    return get_settings().otp_rate_limit_window_minutes * 60


def check_rate_limit_email(email: str) -> bool:
    """Return True if the email is still under its OTP rate limit (records nothing)."""
    return otp_request_limiter.within_limit(
        f"otp:email:{email.lower()}",
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
        f"otp:email:{email.lower()}", window_seconds=_window_seconds()
    )


def record_rate_limit_ip(ip: str) -> None:
    """Record an OTP request for this IP (called only after the mail is actually sent)."""
    otp_request_limiter.record(f"otp:ip:{ip}", window_seconds=_window_seconds())


def generate_otp() -> str:
    """Generate a numeric OTP of the configured length using a CSPRNG."""
    length = get_settings().otp_length
    return "".join(secrets.choice("0123456789") for _ in range(length))


def store_otp(email: str) -> str:
    """Generate and store an OTP for ``email`` (overwrites any prior code).

    Returns the raw OTP so the caller can email it.
    """
    code = generate_otp()
    expires_at = time.time() + (get_settings().otp_ttl_minutes * 60)
    with _store_lock:
        _otp_store[email.lower()] = {
            "code": code,
            "expires_at": expires_at,
            "used": False,
        }
    return code


def invalidate_otp(email: str) -> None:
    """Remove any stored OTP for the email (e.g. after failed email delivery)."""
    with _store_lock:
        _otp_store.pop(email.lower(), None)


def verify_otp(email: str, code: str) -> bool:
    """Verify the OTP for ``email``; mark it used on success (single-use).

    Returns True only when a matching, unexpired, unused code is presented.
    """
    email_lower = email.lower()
    with _store_lock:
        entry = _otp_store.get(email_lower)
        if not entry:
            return False
        if entry["used"]:
            return False
        if time.time() > entry["expires_at"]:
            return False
        if not secrets.compare_digest(entry["code"], code.strip()):
            return False
        entry["used"] = True
    return True


def reset_otp_state() -> None:
    """Clear all OTP codes and OTP rate-limit state (test isolation helper)."""
    with _store_lock:
        _otp_store.clear()
    otp_request_limiter.reset()
