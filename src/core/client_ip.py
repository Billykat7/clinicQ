"""The one client-IP resolver (Issue #179, M30).

Before this module there were **five**: `request_logging`, `auth`, `web/routes`, `clinicq` and
`applications` each had a private `_client_ip`, and they did not agree. Two of them mattered:

* `src/api/v1/routes/auth.py` returned `request.client.host` with the comment *"no proxy header
  trust here"*. That function keyed **both** the OTP per-IP limiter and the password-login per-IP
  limiter. Behind `infra/nginx/platform.conf` every request presents nginx's address, so the per-IP
  budget was a single bucket shared by the entire internet: it isolated no attacker — precisely the
  password-spraying case it was written for — and a burst of legitimate sign-ins returned 429 to
  everybody. It also stored that address as `refresh_token.sign_in_ip`, so the session audit trail
  recorded the proxy.
* `src/core/request_logging.py` did honour `X-Forwarded-For` behind `TRUST_PROXY_HEADERS`, but read
  `parts[0]` — the **leftmost** element. That is the spoofable one; see below.

Pen-test finding **F-04** raised the question and closed it as "deployment guidance". The guidance
was right and the code path that most needed it was never changed. This module is that change.

## Which element of `X-Forwarded-For` is the client

`X-Forwarded-For` is append-only and **client-supplied at the left**. Our nginx sets

    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

which is `$http_x_forwarded_for, $remote_addr` — it appends *the address it actually saw*. So for a
client that sends no header at all the app receives `<client>`, and for a client that forges
`X-Forwarded-For: 1.2.3.4` the app receives `1.2.3.4, <client>`. The **leftmost** element is
therefore attacker-controlled and the **rightmost** is the one our own proxy wrote.

Reading `parts[0]` hands an attacker a free limiter key per forged value — unlimited buckets, so no
budget ever fills. This module counts from the right instead, by
:attr:`~src.core.config.Settings.trusted_proxy_hops`: with one trusted hop (nginx facing the
internet) the client is the last element; with two (a CDN in front of nginx, and the CDN is also
trusted) it is the second-to-last, because the last was written by nginx and names the CDN.

If the header carries fewer elements than there are trusted hops, the header is not shaped the way
the deployment says it is, and we fall back to the socket peer rather than believe it — fail closed.

## Why `TRUST_PROXY_HEADERS` stays off by default

The whole header is client-supplied on a deployment with no proxy in front. Defaulting the flag on
would turn an attacker-controlled string into an authorization input — the key that decides whose
rate-limit budget is spent and what the audit trail records — for every deployment that is not
behind a proxy. It is a deployment fact, so it stays an explicit deployment decision.
"""

from __future__ import annotations

from fastapi import Request

from src.core.config import Settings, get_settings

#: The header our reverse proxy appends to. Kept as a constant so the two places that read it
#: (this resolver and its tests) cannot drift on casing or spelling.
FORWARDED_FOR_HEADER = "x-forwarded-for"


def _peer_ip(request: Request) -> str | None:
    """Return the transport-level peer address, or ``None`` when there is no client."""
    return request.client.host if request.client else None


def resolve_client_ip(request: Request, settings: Settings | None = None) -> str | None:
    """Return the caller's IP address — the single source of truth for the whole application.

    Honours ``X-Forwarded-For`` only when :attr:`Settings.trust_proxy_headers` is on, and then
    takes the element written by the deployment's own outermost trusted proxy rather than the
    leftmost (client-supplied) one. Falls back to the socket peer whenever the header is absent,
    empty, or shorter than the configured proxy depth.

    Args:
        request: The incoming request.
        settings: Injected settings; resolved from :func:`get_settings` when omitted.

    Returns:
        The client's IP as a string, or ``None`` when it cannot be determined at all (an ASGI
        transport with no client, e.g. some test harnesses).
    """
    cfg = settings if settings is not None else get_settings()
    if not cfg.trust_proxy_headers:
        return _peer_ip(request)

    raw = request.headers.get(FORWARDED_FOR_HEADER)
    if not raw:
        return _peer_ip(request)

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    hops = cfg.trusted_proxy_hops
    if len(parts) < hops:
        # The header is not shaped the way the deployment claims — trust the socket, not the claim.
        return _peer_ip(request)
    return parts[-hops]


def client_ip_or_unknown(request: Request, settings: Settings | None = None) -> str:
    """:func:`resolve_client_ip`, with a fixed placeholder when the address is unavailable.

    Rate-limit keys must be strings, and a ``None`` key would silently merge every unaddressable
    caller into whatever bucket ``str(None)`` happens to produce. Naming the placeholder makes that
    bucket explicit and greppable in logs.
    """
    return resolve_client_ip(request, settings) or "unknown"
