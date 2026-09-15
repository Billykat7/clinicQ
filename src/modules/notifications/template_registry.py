"""The versioned, multi-language registry of patient message templates (Issue 66).

This is the registry :mod:`src.modules.notifications.templates` already was, for the patient's ticket
messages, extended with **language** and **version**. It is not a second registry: ``templates.render``
still renders every message, and for these templates it asks here.

**Keyed by** ``(template, channel, language)``, each with numbered, immutable versions:

* the words live in ``src/locales/<language>/notifications.toml``;
* :func:`current` returns the newest version for a key, writing a file version it has not stored yet into
  ``notification_template_version``, so every version ever sent stays reproducible;
* :func:`publish` stores a version written in the admin editor, always numbered above everything before it.

**A strict variable contract, enforced at registration** (:func:`validate`): when the locale files are
loaded, and when an edit is published. So a mistake fails the test suite or the editor, never a patient's
message. A version is refused when:

* it uses a blank the event does not provide (``{nme}``), or a blank with an attribute, an index, a
  conversion or a format spec (``{number.__class__}``). That is a typo, or an attempt to reach inside a
  value;
* it does not say the ticket number;
* a web push uses anything but ``{number}`` and ``{clinic}`` (a lock screen is public, Issue 64);
* an SMS does not fit one GSM 7-bit segment with the longest names the database allows (Issue 65);
* it has a subject where the channel has none, or lacks one where it needs one.

**Rendering** fills the blanks from the context stored on the ledger row, shortening the clinic, queue and
room for SMS. The same version and the same context always give the same words.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from string import Formatter
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import (
    DEFAULT_NOTIFICATION_LANGUAGE,
    NOTIFICATION_LANGUAGES,
    PATIENT_EVENT_TEMPLATE,
    BoardLanguage,
    NotificationChannel,
    NotificationTemplate,
    TemplateSource,
)
from src.commons.time import now_sast
from src.core.config import get_settings
from src.database.models.notification_template import NotificationTemplateVersion
from src.modules.notifications.schemas import RenderedMessage
from src.modules.notifications.sms_segments import (
    SegmentCount,
    SmsEncoding,
    count_segments,
    to_gsm7,
)

#: Where the locale files are.
LOCALES_DIR: Final = Path(__file__).resolve().parents[2] / "locales"
#: The templates this registry holds: every patient ticket message.
PATIENT_TEMPLATES: Final = frozenset(PATIENT_EVENT_TEMPLATE.values())
#: The only path a web push's reply buttons may send to (Issue 82).
REPLY_PATH_PREFIX: Final = "/api/v1/appointments/replies/"
#: The channels a patient message is written for.
PATIENT_CHANNELS: Final = (
    NotificationChannel.SMS,
    NotificationChannel.WHATSAPP,
    NotificationChannel.WEB_PUSH,
)

#: The blanks every ticket message may use.
_TICKET_BLANKS: Final = frozenset({"app", "number", "clinic", "queue", "room", "where"})
#: The blanks each template may use, beyond which a version is refused.
VARIABLES: Final[Mapping[NotificationTemplate, frozenset[str]]] = {
    NotificationTemplate.TICKET_NEXT: _TICKET_BLANKS,
    NotificationTemplate.TICKET_CALLED: _TICKET_BLANKS,
    NotificationTemplate.TICKET_RECALLED: _TICKET_BLANKS | {"minutes"},
    NotificationTemplate.TICKET_NO_SHOW: _TICKET_BLANKS,
    NotificationTemplate.TICKET_TRANSFERRED: _TICKET_BLANKS | {"wait"},
    NotificationTemplate.TICKET_CANCELLED: _TICKET_BLANKS,
    NotificationTemplate.TICKET_LEAVE_NOW: _TICKET_BLANKS | {"wait", "minutes"},
    NotificationTemplate.TICKET_FEEDBACK: _TICKET_BLANKS,
    # A booking's reference is its {number}, and {when} its time (Issue 81).
    NotificationTemplate.APPOINTMENT_BOOKED: _TICKET_BLANKS | {"when"},
    NotificationTemplate.APPOINTMENT_LAPSED: _TICKET_BLANKS | {"when"},
    NotificationTemplate.APPOINTMENT_REMINDER_24H: _TICKET_BLANKS | {"when"},
    NotificationTemplate.APPOINTMENT_REMINDER_2H: _TICKET_BLANKS | {"when"},
}
#: What a web push may say, whatever the template: a lock screen is public (Issue 64).
PUSH_VARIABLES: Final = frozenset({"number", "clinic"})
#: What every message must say.
REQUIRED: Final = frozenset({"number"})

#: How long a clinic, and a queue or room, may be inside an SMS (Issue 65).
SMS_CLINIC_CHARS: Final = 28
SMS_PLACE_CHARS: Final = 18

#: The longest values the tables allow and the estimator writes: the worst case an SMS must fit in.
WORST_CASE: Final[Mapping[str, object]] = {
    "number": "Z" * 10,
    "clinic": "X" * 200,
    "queue": "Q" * 80,
    "room": "R" * 40,
    # A recall's minutes are at most 60; a stated trip (Issue 86) at most MAX_TRAVEL_MINUTES.
    "minutes": 180,
    "wait": "~180–240 min (approximate)",
    "when": "Wed 30 Sep 23:45",
}
#: What the editor's preview is rendered with: a plausible ticket, so the words read as a patient sees them.
SAMPLE: Final[Mapping[str, object]] = {
    "number": "A043",
    "clinic": "Zola Community Clinic",
    "queue": "General consultation",
    "room": "Room 4",
    "minutes": 5,
    "wait": "~15–25 min",
    "when": "Tue 6 Oct 10:30",
    "page_url": "/t/sample",
}


class TemplateError(ValueError):
    """A template version the registry will not register, with a sentence saying why."""


@dataclass(frozen=True, slots=True)
class TemplateText:
    """The words of one version, before it is stored."""

    template: NotificationTemplate
    channel: NotificationChannel
    language: BoardLanguage
    version: int
    body: str
    subject: str | None = None
    reviewed_by: str | None = None


def _fit(value: object, limit: int) -> str:
    """``value`` as text, shortened to ``limit`` with a GSM-safe "..." when longer."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def blanks(text: str) -> set[str]:
    """The blanks ``text`` uses.

    Raises:
        TemplateError: A blank that is not a plain name (an attribute, an index, a conversion, a format spec,
            an unnamed ``{}``), or a stray brace.
    """
    found: set[str] = set()
    try:
        parts = list(Formatter().parse(text))
    except ValueError as exc:
        raise TemplateError(f"The braces do not match: {exc}.") from exc
    for _literal, field, spec, conversion in parts:
        if field is None:
            continue
        if not field.isidentifier() or spec or conversion:
            raise TemplateError(
                f"{{{field}}} is not a plain blank: use a name from the list, like {{number}}."
            )
        found.add(field)
    return found


def allowed(
    template: NotificationTemplate, channel: NotificationChannel
) -> frozenset[str]:
    """The blanks a version of ``template`` for ``channel`` may use."""
    if channel is NotificationChannel.WEB_PUSH:
        return PUSH_VARIABLES
    return VARIABLES[template]


def values(context: Mapping[str, Any], channel: NotificationChannel) -> dict[str, str]:
    """The blanks' values for one message, from its stored context; shortened for SMS."""
    where = context.get("room") or context.get("queue") or ""
    sms = channel is NotificationChannel.SMS
    return {
        "app": get_settings().app_name,
        "number": str(context.get("number", "")),
        "clinic": _fit(context.get("clinic", ""), SMS_CLINIC_CHARS)
        if sms
        else str(context.get("clinic", "")),
        "queue": _fit(context.get("queue", ""), SMS_PLACE_CHARS)
        if sms
        else str(context.get("queue", "")),
        "room": _fit(context.get("room") or "", SMS_PLACE_CHARS)
        if sms
        else str(context.get("room") or ""),
        "where": _fit(where, SMS_PLACE_CHARS) if sms else str(where),
        "minutes": str(context.get("minutes", "")),
        "wait": str(context.get("wait", "")),
        "when": str(context.get("when", "")),
    }


def render(
    template: NotificationTemplate,
    channel: NotificationChannel,
    body: str,
    subject: str | None,
    context: Mapping[str, Any],
) -> RenderedMessage:
    """The message ``body`` and ``subject`` say for this context. The link and tag are the channel's."""
    filled = values(context, channel)
    page_url = context.get("page_url")
    push = channel is NotificationChannel.WEB_PUSH
    reply_url = context.get("reply_url")
    return RenderedMessage(
        text=body.format_map(filled),
        subject=subject.format_map(filled) if subject else None,
        link=page_url
        # A ticket page, or a feedback answer page (Issue 87): never another site, never an id.
        if push and isinstance(page_url, str) and page_url.startswith(("/t/", "/f/"))
        else None,
        tag=f"clinicq-ticket-{filled['number']}" if push else None,
        # A reminder's Confirm and Cancel buttons (Issue 82): only ever this site's own reply path.
        reply_url=reply_url
        if push
        and isinstance(reply_url, str)
        and reply_url.startswith(REPLY_PATH_PREFIX)
        else None,
    )


def validate(text: TemplateText) -> SegmentCount | None:
    """Refuse a version that breaks the variable contract; return its worst-case SMS size, for an SMS.

    Raises:
        TemplateError: Why the version cannot be registered.
    """
    if text.template not in PATIENT_TEMPLATES:
        raise TemplateError(f"{text.template.value} is not a patient message template.")
    if text.channel not in PATIENT_CHANNELS:
        raise TemplateError(f"{text.channel.value} is not a patient message channel.")
    if not text.body.strip():
        raise TemplateError("The message has no words.")
    used = blanks(text.body) | (blanks(text.subject) if text.subject else set())
    unknown = used - allowed(text.template, text.channel)
    if unknown:
        names = ", ".join(f"{{{name}}}" for name in sorted(unknown))
        where = (
            "a web push"
            if text.channel is NotificationChannel.WEB_PUSH
            else "this message"
        )
        raise TemplateError(
            f"{names} cannot be used in {where}: it is not in the message's variables."
        )
    missing = REQUIRED - blanks(text.body)
    if missing:
        raise TemplateError("The message must say the ticket number: add {number}.")
    if (
        text.channel is NotificationChannel.WEB_PUSH
        and not (text.subject or "").strip()
    ):
        raise TemplateError("A web push needs a title.")
    if text.channel is not NotificationChannel.WEB_PUSH and text.subject:
        raise TemplateError(f"A {text.channel.value} message has no title.")
    if text.channel is not NotificationChannel.SMS:
        return None
    worst = count_segments(
        to_gsm7(render(text.template, text.channel, text.body, None, WORST_CASE).text)
    )
    if worst.encoding is not SmsEncoding.GSM7 or worst.segments > 1:
        raise TemplateError(
            f"With the longest clinic and room names this SMS is {worst.units} characters "
            f"({worst.encoding.value}), more than one segment: shorten it."
        )
    return worst


def languages() -> tuple[BoardLanguage, ...]:
    """The notification languages that have a locale file, English first."""
    return tuple(
        language
        for language in NOTIFICATION_LANGUAGES
        if (LOCALES_DIR / language.value / "notifications.toml").is_file()
    )


@cache
def builtin(
    language: BoardLanguage,
) -> Mapping[tuple[NotificationTemplate, NotificationChannel], TemplateText]:
    """Every template version in ``language``'s locale file, validated: the registration step.

    Raises:
        TemplateError: A version breaks the contract, naming the file, the template and the channel.
    """
    path = LOCALES_DIR / language.value / "notifications.toml"
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    reviewed_by = data.get("reviewed_by")
    texts: dict[tuple[NotificationTemplate, NotificationChannel], TemplateText] = {}
    for key, channels in data.items():
        if not isinstance(channels, dict):
            continue
        for channel_key, entry in channels.items():
            try:
                text = TemplateText(
                    template=NotificationTemplate(key),
                    channel=NotificationChannel(channel_key),
                    language=language,
                    version=int(entry["version"]),
                    body=str(entry["body"]),
                    subject=entry.get("subject"),
                    reviewed_by=reviewed_by,
                )
                validate(text)
            except (KeyError, ValueError) as exc:
                raise TemplateError(
                    f"{path.name} [{key}.{channel_key}] ({language.value}): {exc}"
                ) from exc
            texts[(text.template, text.channel)] = text
    return texts


def builtin_text(
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: BoardLanguage = DEFAULT_NOTIFICATION_LANGUAGE,
) -> TemplateText:
    """The locale file's version for a key, falling back to English when ``language`` has none."""
    text = builtin(language).get((template, channel))
    if text is None and language is not DEFAULT_NOTIFICATION_LANGUAGE:
        text = builtin(DEFAULT_NOTIFICATION_LANGUAGE).get((template, channel))
    if text is None:
        raise TemplateError(f"No {channel.value} words for {template.value}.")
    return text


def _stored(db: Session, text: TemplateText) -> NotificationTemplateVersion:
    """The row for a locale file version, written the first time it is met."""
    row = db.execute(
        select(NotificationTemplateVersion).where(
            NotificationTemplateVersion.template_key == text.template.value,
            NotificationTemplateVersion.channel == text.channel.value,
            NotificationTemplateVersion.language == text.language.value,
            NotificationTemplateVersion.version == text.version,
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = NotificationTemplateVersion(
        template_key=text.template.value,
        channel=text.channel.value,
        language=text.language.value,
        version=text.version,
        subject=text.subject,
        body=text.body,
        source=TemplateSource.BUILTIN.value,
        reviewed_by=text.reviewed_by,
        created_at=now_sast(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        # Another worker stored it first; theirs is the same words.
        return _stored(db, text)
    return row


def current(
    db: Session,
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: BoardLanguage,
) -> NotificationTemplateVersion:
    """The version to send now: the newest stored for the key, never older than the locale file's.

    A language with no words for this template and channel falls back to English, and the row returned says
    which language it is.
    """
    text = builtin_text(template, channel, language)
    file_row = _stored(db, text)
    newest = db.execute(
        select(NotificationTemplateVersion)
        .where(
            NotificationTemplateVersion.template_key == template.value,
            NotificationTemplateVersion.channel == channel.value,
            NotificationTemplateVersion.language == text.language.value,
        )
        .order_by(NotificationTemplateVersion.version.desc())
        .limit(1)
    ).scalar_one()
    return newest if newest.version > file_row.version else file_row


def publish(
    db: Session,
    *,
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: BoardLanguage,
    body: str,
    subject: str | None,
    author: str,
    reviewed_by: str | None = None,
    moment: datetime | None = None,
) -> NotificationTemplateVersion:
    """Register a version written in the editor; it is sent from the next message on. The caller commits.

    Raises:
        TemplateError: It breaks the variable contract (see :func:`validate`).
    """
    text = TemplateText(
        template=template,
        channel=channel,
        language=language,
        version=0,
        body=body.strip(),
        subject=(subject or "").strip() or None,
        reviewed_by=(reviewed_by or "").strip() or None,
    )
    validate(text)
    if language in languages():
        _stored(db, builtin_text(template, channel, language))
    highest = db.execute(
        select(func.max(NotificationTemplateVersion.version)).where(
            NotificationTemplateVersion.template_key == template.value,
            NotificationTemplateVersion.channel == channel.value,
            NotificationTemplateVersion.language == language.value,
        )
    ).scalar_one()
    row = NotificationTemplateVersion(
        template_key=template.value,
        channel=channel.value,
        language=language.value,
        version=(highest or 0) + 1,
        subject=text.subject,
        body=text.body,
        source=TemplateSource.EDITED.value,
        created_by=author,
        reviewed_by=text.reviewed_by,
        created_at=moment or now_sast(),
    )
    db.add(row)
    db.flush()
    return row


def render_version(
    row: NotificationTemplateVersion, context: Mapping[str, Any]
) -> RenderedMessage:
    """The message a stored version gives for a context: exactly what was, or will be, sent."""
    return render(
        NotificationTemplate(row.template_key),
        NotificationChannel(row.channel),
        row.body,
        row.subject,
        context,
    )
