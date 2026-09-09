"""Short-lived signed download links for the document service (Issue #70).

A consolidated document is never served from a public URL. Instead an authorised, RBAC-checked
caller mints a **signed, time-limited link**; the download endpoint that streams the bytes takes no
session at all and trusts only the link. This module is the two halves of that link:
:func:`create_document_download_token` (minting) and :func:`decode_document_download_token`
(verification).

It mirrors ``src.modules.tenants.download_links`` exactly — a typed HS256 JWT using the same
primitive and secret as the activation/reset links in :mod:`src.core.security` — carrying:

* ``sub`` — the document id the link is scoped to, so a token minted for one document can never be
  replayed against another;
* ``act`` — the acting user (their token ``sub``/email), captured at mint time so the
  unauthenticated download can still write a truthful actor into the audit log;
* ``type`` — pinned to :data:`TokenType.DOCUMENT_SERVICE_DOWNLOAD` so an ordinary access token, or a
  per-module download link, can never be passed off as a document-service link;
* ``exp`` / ``iat`` — a lifetime of ``document_link_expire_seconds`` (default 5 minutes), enforced
  by the JWT library on decode.

Verification returns the document id and actor on success and ``None`` on any failure (expired,
tampered, wrong type, missing claim) so the caller reveals nothing about why.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.commons.enums import TokenType
from src.core.config import get_settings


def create_document_download_token(
    document_id: str, *, actor: str | None = None
) -> str:
    """Mint a short-lived signed link token for one document.

    Args:
        document_id: The document the link grants access to (becomes the ``sub`` claim).
        actor: Identifier of the user minting the link (their token ``sub``/email), recorded so the
            later unauthenticated download can attribute the audit entry.

    Returns:
        A typed HS256 JWT valid for ``document_link_expire_seconds``.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(seconds=settings.document_link_expire_seconds)
    payload: dict[str, Any] = {
        "sub": document_id,
        "type": TokenType.DOCUMENT_SERVICE_DOWNLOAD.value,
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }
    if actor:
        payload["act"] = actor
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_document_download_token(token: str) -> tuple[str, str | None] | None:
    """Validate a download link token, returning ``(document_id, actor)`` or ``None``.

    The token must decode, be unexpired, carry the ``DOCUMENT_SERVICE_DOWNLOAD`` type and a ``sub``.
    Any failure returns ``None`` (the caller maps that to a 403) so a bad link reveals nothing about
    why it failed or whether the document exists.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != TokenType.DOCUMENT_SERVICE_DOWNLOAD.value:
        return None
    document_id = payload.get("sub")
    if not document_id:
        return None
    actor = payload.get("act")
    return document_id, actor if isinstance(actor, str) else None
