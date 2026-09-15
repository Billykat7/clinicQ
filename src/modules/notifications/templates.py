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


def _render_ticket_recalled_sms(context: dict[str, Any]) -> RenderedMessage:
    """A called patient has not arrived and has been called once more (Issue 43).

    Says what happened, where to go and what happens next, in one SMS. Expects ``number``,
    ``clinic``, ``queue`` and ``minutes``; ``room`` when the queue has one.
    """
    app_name = get_settings().app_name
    where = context.get("room") or context["queue"]
    return RenderedMessage(
        text=(
            f"{app_name}: ticket {context['number']} at {context['clinic']} was called and you "
            f"have not arrived. We have called you once more: please come to {where} now. "
            f"If you are not there within {context['minutes']} minutes the ticket will be marked "
            "missed."
        )
    )


def _render_ticket_no_show_sms(context: dict[str, Any]) -> RenderedMessage:
    """A recalled patient still did not arrive: the ticket is closed, and how to join again (Issue 43)."""
    app_name = get_settings().app_name
    return RenderedMessage(
        text=(
            f"{app_name}: ticket {context['number']} at {context['clinic']} was marked missed "
            "because you did not arrive after being called twice. To join the queue again, use "
            f"{app_name} on your phone, dial the {app_name} USSD code, or ask at the front desk."
        )
    )


def _render_ticket_transferred_sms(context: dict[str, Any]) -> RenderedMessage:
    """A patient moved on to the next queue of their visit (Issue 45): where, which number, how long."""
    app_name = get_settings().app_name
    return RenderedMessage(
        text=(
            f"{app_name}: at {context['clinic']} you are now in the {context['queue']} queue as "
            f"ticket {context['number']}. Expected wait {context['wait']}. You do not need to join "
            "again."
        )
    )


def _render_ticket_next_sms(context: dict[str, Any]) -> RenderedMessage:
    """The patient is first in line (Issue 63): time to be at the clinic, and where.

    Expects ``number``, ``clinic`` and ``queue``; ``room`` when the queue has one.
    """
    app_name = get_settings().app_name
    where = context.get("room") or context["queue"]
    return RenderedMessage(
        subject="You are next",
        text=(
            f"{app_name}: ticket {context['number']} at {context['clinic']}, you are next. "
            f"Please be ready at {where}."
        ),
    )


def _render_ticket_called_sms(context: dict[str, Any]) -> RenderedMessage:
    """The patient has been called (Issue 63): come in now, and where to go."""
    app_name = get_settings().app_name
    where = context.get("room") or context["queue"]
    return RenderedMessage(
        subject="Please come in now",
        text=(
            f"{app_name}: ticket {context['number']}, please come in now to {where} at "
            f"{context['clinic']}."
        ),
    )


def _render_ticket_cancelled_sms(context: dict[str, Any]) -> RenderedMessage:
    """The clinic cancelled the patient's ticket (Issue 63): say so, and how to join again."""
    app_name = get_settings().app_name
    return RenderedMessage(
        subject="Ticket cancelled",
        text=(
            f"{app_name}: ticket {context['number']} at {context['clinic']} was cancelled by the "
            f"clinic. To join again, use {app_name} or ask at the front desk."
        ),
    )


#: The patient's ticket messages (Issues 43, 45, 63). Until Issue 66 writes a richer variant per
#: channel, WhatsApp carries the same words as the SMS; web push says less (``_PUSH_WORDS``).
_TICKET_RENDERERS: dict[NotificationTemplate, Renderer] = {
    NotificationTemplate.TICKET_NEXT: _render_ticket_next_sms,
    NotificationTemplate.TICKET_CALLED: _render_ticket_called_sms,
    NotificationTemplate.TICKET_RECALLED: _render_ticket_recalled_sms,
    NotificationTemplate.TICKET_NO_SHOW: _render_ticket_no_show_sms,
    NotificationTemplate.TICKET_TRANSFERRED: _render_ticket_transferred_sms,
    NotificationTemplate.TICKET_CANCELLED: _render_ticket_cancelled_sms,
}

#: What each ticket message says as a web push (Issue 64): a title, and a body naming the ticket number
#: and the clinic, nothing else. A lock screen is read by whoever picks the phone up, so no queue name
#: (a queue can be "HIV clinic"), room, reason or name. ``{number}`` and ``{clinic}`` are the only blanks.
_PUSH_WORDS: dict[NotificationTemplate, tuple[str, str]] = {
    NotificationTemplate.TICKET_NEXT: (
        "You are next",
        "Ticket {number} at {clinic}. Please be ready.",
    ),
    NotificationTemplate.TICKET_CALLED: (
        "Please come in now",
        "Ticket {number} at {clinic}.",
    ),
    NotificationTemplate.TICKET_RECALLED: (
        "You have been called again",
        "Ticket {number} at {clinic}. Please come in now.",
    ),
    NotificationTemplate.TICKET_NO_SHOW: (
        "Ticket marked missed",
        "Ticket {number} at {clinic}. Ask at the front desk to join again.",
    ),
    NotificationTemplate.TICKET_TRANSFERRED: (
        "Your visit continues",
        "Ticket {number} at {clinic}. Open to see where to go.",
    ),
    NotificationTemplate.TICKET_CANCELLED: (
        "Ticket cancelled",
        "Ticket {number} at {clinic} was cancelled by the clinic.",
    ),
}


def _push_renderer(template: NotificationTemplate) -> Renderer:
    """A web push renderer for ``template``: :data:`_PUSH_WORDS`, a link to the ticket page, a tag."""
    title, body = _PUSH_WORDS[template]

    def render_push(context: dict[str, Any]) -> RenderedMessage:
        """Title and body from the number and clinic only; tapping opens the ticket's page."""
        number, clinic = str(context["number"]), str(context["clinic"])
        page_url = context.get("page_url")
        return RenderedMessage(
            subject=title,
            text=body.format(number=number, clinic=clinic),
            link=page_url
            if isinstance(page_url, str) and page_url.startswith("/t/")
            else None,
            tag=f"clinicq-ticket-{number}",
        )

    return render_push


#: The channels a patient notification can go out on with the SMS words. Web push has its own.
_PATIENT_CHANNELS = (
    NotificationChannel.SMS,
    NotificationChannel.WHATSAPP,
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
    **{
        (channel, template): renderer
        for channel in _PATIENT_CHANNELS
        for template, renderer in _TICKET_RENDERERS.items()
    },
    **{
        (NotificationChannel.WEB_PUSH, template): _push_renderer(template)
        for template in _PUSH_WORDS
    },
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
    renderer = _RENDERERS.get((channel, template))
    if renderer is None:
        raise KeyError(
            f"No notification renderer registered for {channel.value}/{template.value} "
            "and the payload is not pre-rendered."
        )
    return renderer(context)
