"""Template registry for the notification service (Issue #67).

One place that turns a ``(template_key, channel, context)`` into a :class:`RenderedMessage`, so
message bodies are defined once and a queued notification can be *re-rendered* on a later retry
from the context snapshot on its row — the retry sweep never needs the originating domain objects,
which may since have changed.

Two rendering modes share this registry:

* **Pre-rendered passthrough.** The migrated email path (``src.core.email_send``) still builds its
  rich branded HTML with the existing renderers and stores the result under the reserved payload
  keys :data:`_SUBJECT` / :data:`_TEXT` / :data:`_HTML`; :func:`render_notification` returns those
  verbatim. This keeps every existing transactional email byte-for-byte identical while moving
  *delivery* into the one service.
* **Context rendering.** SMS templates (and any new email flow) register a renderer keyed by
  ``(channel, template_key)`` that builds the body from the stored context — this is the registry
  proper, the home new templates are added to.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.commons.enums import NotificationChannel, NotificationTemplate
from src.core.config import get_settings
from src.modules.notifications import template_registry
from src.modules.notifications.schemas import RenderedMessage

# Reserved payload keys carrying an already-rendered body (the passthrough mode above).
_SUBJECT = "_subject"
_TEXT = "_text"
_HTML = "_html"

# A context renderer takes the notification's stored payload and returns a rendered message.
Renderer = Callable[[dict[str, Any]], RenderedMessage]


def _render_otp_sms(context: dict[str, Any]) -> RenderedMessage:
    """Render the one-time sign-in code as a single SMS line.

    Deliberately terse and link-free: the code and its expiry, nothing an interceptor could act on
    beyond the code itself. Expects ``code`` and (optionally) ``ttl_minutes`` in the context.
    """
    app_name = get_settings().app_name
    code = str(context["code"])
    ttl = context.get("ttl_minutes")
    tail = f" It expires in {ttl} minutes." if ttl else ""
    return RenderedMessage(
        text=f"{app_name}: your sign-in code is {code}.{tail} Do not share it."
    )


def _render_generic_sms(context: dict[str, Any]) -> RenderedMessage:
    """Render an ad-hoc SMS from a caller-supplied ``text`` context field."""
    return RenderedMessage(text=str(context["text"]))


def _render_staff_invitation_sms(context: dict[str, Any]) -> RenderedMessage:
    """Render a staff invitation as one SMS line: what it is, the link, and how long it lasts.

    The link is a credential (whoever opens it sets that account's password), so it is a secret
    field on the ledger — the row keeps the send, never the link (Issue 22). Expects ``link`` and
    ``hours``.
    """
    app_name = get_settings().app_name
    hours = context.get("hours")
    tail = f" It expires in {hours} hours." if hours else ""
    return RenderedMessage(
        text=(
            f"{app_name}: you have been invited to join a clinic. "
            f"Open {context['link']} to set your password.{tail} Do not share this link."
        )
    )


# Context renderers, keyed by (channel, template). New SMS templates (and any future
# context-rendered email) are registered here — the single registry the issue calls for.
_RENDERERS: dict[tuple[NotificationChannel, NotificationTemplate], Renderer] = {
    (NotificationChannel.SMS, NotificationTemplate.OTP_SIGN_IN): _render_otp_sms,
    (NotificationChannel.SMS, NotificationTemplate.GENERIC): _render_generic_sms,
    (
        NotificationChannel.SMS,
        NotificationTemplate.STAFF_INVITATION,
    ): _render_staff_invitation_sms,
}


def prerendered_payload(
    *, subject: str | None, text: str, html: str | None
) -> dict[str, Any]:
    """Build a payload carrying an already-rendered body for the passthrough mode.

    Used by the migrated email path: the branded HTML is rendered by the existing
    ``src.core.email_send`` helpers, then handed to the service as context so the row is
    re-deliverable by the retry sweep without re-rendering.
    """
    payload: dict[str, Any] = {_TEXT: text}
    if subject is not None:
        payload[_SUBJECT] = subject
    if html is not None:
        payload[_HTML] = html
    return payload


def render(
    channel: NotificationChannel,
    template: NotificationTemplate,
    context: dict[str, Any],
) -> RenderedMessage:
    """Render a message for ``channel``/``template`` from ``context``.

    Returns the pre-rendered body when the context carries one (passthrough mode); otherwise
    dispatches to the registered context renderer.

    Raises:
        KeyError: When no renderer is registered for the channel/template and the context is not
            pre-rendered — a programming error (a new template without a renderer).
    """
    if _TEXT in context:
        return RenderedMessage(
            text=str(context[_TEXT]),
            subject=context.get(_SUBJECT),
            html=context.get(_HTML),
        )
    if template in template_registry.PATIENT_TEMPLATES:
        # A patient's ticket message (Issue 66): the locale file's current English words. The service sends
        # the version it stored on the ledger row instead (template_registry.render_version).
        text = template_registry.builtin_text(template, channel)
        return template_registry.render(
            template, channel, text.body, text.subject, context
        )
    renderer = _RENDERERS.get((channel, template))
    if renderer is None:
        raise KeyError(
            f"No notification renderer registered for {channel.value}/{template.value} "
            "and the payload is not pre-rendered."
        )
    return renderer(context)
