"""FastAPI dependencies that apply a rate limit to a route (Issue #179, M30).

Kept apart from :mod:`src.core.rate_limit` so that module stays a plain limiter with no web
framework in it — the limiters are used from services and tests as well as routes.

## Why signed-link minting is limited at all

Every private document, photo, statement and attachment leaves this system the same way: an
RBAC- and scope-checked caller mints a short-lived signed JWT and the streaming endpoint trusts only
that link. The mint routes are therefore *authenticated*, which is why the M17 checklist recorded
them as "RBAC-gated + short TTL" and left it there.

That is the right control against an outsider and the wrong one against the case that actually
matters here: a **stolen session**. An attacker holding one valid session can mint links as fast as
the API answers, and every minted link keeps working for its full TTL after the session is revoked.
A per-IP budget turns "drain the document store in a minute" into "drain it slowly enough to be
noticed", which is the whole ambition of a rate limit on an authenticated surface.

The budget is deliberately loose (60/minute by default): a person opening documents one at a time
never approaches it, and a script enumerating a tenant's file list immediately does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from src.core.client_ip import client_ip_or_unknown
from src.core.config import Settings, get_settings
from src.core.rate_limit import signed_link_limiter

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def enforce_signed_link_mint_limit(
    request: Request, settings: SettingsDep
) -> None:
    """Rate-limit signed-link minting per client IP; raise 429 once the window is spent.

    Keyed on the client IP rather than the session, on purpose: a stolen session is the threat, and
    an attacker who has one can also rotate it. The address they are dialling from is the thing they
    cannot trivially change — and it is now the *real* address, because
    :func:`~src.core.client_ip.resolve_client_ip` is the one resolver (Issue #179).
    """
    allowed = signed_link_limiter.check_and_record(
        f"link:{client_ip_or_unknown(request, settings)}",
        limit=settings.signed_link_mint_rate_limit_per_ip,
        window_seconds=settings.signed_link_mint_rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many download links requested; please try again shortly.",
        )


#: Declare on any route that mints a signed download link.
SignedLinkMintLimit = Annotated[None, Depends(enforce_signed_link_mint_limit)]
