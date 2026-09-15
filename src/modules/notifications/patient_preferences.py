"""A patient's own notification preferences, changed without an account (Issue 67).

The send-time gate that applies these is :func:`src.modules.notifications.preferences.resolve`; this module
is how they are changed:

* **From the ticket page**, by its unguessable link (Issue 68): :func:`read` and :func:`update`. A patient has
  no account, and the link is what was sent to them. Anyone they share it with can change how they are told,
  as they can follow the ticket, and the page says so. The link grants nothing else: no ticket, no other
  patient, no consent.
* **By replying to an SMS**: :func:`apply_reply`. ``STOP`` (and the other words in
  :data:`~src.commons.enums.SMS_STOP_KEYWORDS`) stops every message on every channel at once, including
  messages already queued or waiting for quiet hours to end, because the gate is asked again at delivery.
  ``START`` undoes it.
* **USSD and WhatsApp** (M10) call the same :func:`update`, naming themselves as the source.

Preferences belong to the patient, not to a clinic: stopping at one clinic stops messages from every clinic,
and they hold when the patient joins a queue somewhere else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from src.commons.enums import (
    NOTIFICATION_LANGUAGES,
    PATIENT_EVENT_TEMPLATE,
    PATIENT_QUIET_HOURS_EXEMPT,
    SMS_START_KEYWORDS,
    SMS_STOP_KEYWORDS,
    NotificationChannel,
    PatientEvent,
    PreferenceSource,
)
from src.commons.phone import InvalidPhoneNumberError
from src.commons.time import now_sast
from src.database.models.patient_notification_preference import (
    PatientNotificationPreference,
)
from src.modules.notifications.schemas import (
    PatientPreferencesIn,
    PatientPreferencesOut,
)

logger = logging.getLogger(__name__)

#: The channels a patient may prefer.
PREFERABLE_CHANNELS = frozenset(
    {
        NotificationChannel.WEB_PUSH,
        NotificationChannel.WHATSAPP,
        NotificationChannel.SMS,
    }
)


class ReplyOutcome(StrEnum):
    """What an SMS reply did."""

    STOPPED = "stopped"
    RESTARTED = "restarted"
    #: A digit from 1 to 5 answering the patient's open post-visit question (Issue 87).
    RATED = "rated"
    IGNORED = "ignored"


class PreferenceError(ValueError):
    """A change the preferences cannot take, with a sentence saying why."""


@dataclass(frozen=True, slots=True)
class ReplyResult:
    """The outcome of one SMS reply, and the patient it applied to (``None`` for an unknown number)."""

    outcome: ReplyOutcome
    patient_id: str | None


def _row(db: Session, patient_id: str) -> PatientNotificationPreference:
    """The patient's preference row, created empty when they have none."""
    row = db.get(PatientNotificationPreference, patient_id)
    if row is None:
        row = PatientNotificationPreference(patient_id=patient_id, muted_events=[])
        db.add(row)
        db.flush()
    return row


def read(db: Session, patient_id: str) -> PatientPreferencesOut:
    """The patient's preferences; the defaults when they never set any."""
    row = db.get(PatientNotificationPreference, patient_id)
    return PatientPreferencesOut(
        opted_out=bool(row and row.opted_out_at),
        opted_out_at=row.opted_out_at if row else None,
        preferred_channel=NotificationChannel(row.preferred_channel)
        if row and row.preferred_channel
        else None,
        language=row.language if row else None,
        quiet_hours_start=row.quiet_hours_start if row else None,
        quiet_hours_end=row.quiet_hours_end if row else None,
        muted_events=[
            PatientEvent(value) for value in (row.muted_events if row else [])
        ],
        quiet_hours_exempt=sorted(
            event
            for event, template in PATIENT_EVENT_TEMPLATE.items()
            if template in PATIENT_QUIET_HOURS_EXEMPT
        ),
        languages=[language.value for language in NOTIFICATION_LANGUAGES],
    )


def update(
    db: Session,
    patient_id: str,
    change: PatientPreferencesIn,
    *,
    source: PreferenceSource,
    now: datetime | None = None,
) -> PatientPreferencesOut:
    """Apply ``change``; only the fields it names move. Takes effect on the next send. The caller commits.

    Raises:
        PreferenceError: A channel a patient cannot be reached on, or a language messages are not written in.
    """
    moment = now or now_sast()
    fields = change.model_fields_set
    if "preferred_channel" in fields and change.preferred_channel not in (
        None,
        *PREFERABLE_CHANNELS,
    ):
        raise PreferenceError("Choose web push, WhatsApp or SMS.")
    codes = {language.value for language in NOTIFICATION_LANGUAGES}
    if (
        "language" in fields
        and change.language is not None
        and change.language not in codes
    ):
        raise PreferenceError(f"Messages are sent in {', '.join(sorted(codes))}.")
    row = _row(db, patient_id)
    if "opted_out" in fields and change.opted_out is not None:
        # Saying stop again keeps the moment the patient first stopped.
        if not change.opted_out:
            row.opted_out_at = None
        elif row.opted_out_at is None:
            row.opted_out_at = moment
    if "preferred_channel" in fields:
        row.preferred_channel = (
            change.preferred_channel.value if change.preferred_channel else None
        )
    if "language" in fields:
        row.language = change.language
    if "quiet_hours_start" in fields:
        row.quiet_hours_start = change.quiet_hours_start
        row.quiet_hours_end = change.quiet_hours_end
    if "muted_events" in fields and change.muted_events is not None:
        row.muted_events = sorted({event.value for event in change.muted_events})
    row.opted_out_via = source.value
    db.flush()
    return read(db, patient_id)


def apply_reply(
    db: Session, phone: str, text: str, *, now: datetime | None = None
) -> ReplyResult:
    """Apply an SMS reply's keyword to the patient who owns ``phone``. The caller commits.

    The first word decides, upper-cased: ``STOP`` (and its synonyms) stops every message, ``START`` undoes it,
    anything else changes nothing. A number no patient has changes nothing either.
    """
    from src.modules.patients import service as patients

    words = text.strip().split()
    keyword = words[0].upper().strip(".!,") if words else ""
    if keyword in SMS_STOP_KEYWORDS:
        wanted = True
    elif keyword in SMS_START_KEYWORDS:
        wanted = False
    else:
        return ReplyResult(ReplyOutcome.IGNORED, None)
    try:
        patient = patients.find_patient(db, phone)
    except InvalidPhoneNumberError:
        patient = None
    if patient is None:
        return ReplyResult(ReplyOutcome.IGNORED, None)
    update(
        db,
        patient.id,
        PatientPreferencesIn(opted_out=wanted),
        source=PreferenceSource.SMS_REPLY,
        now=now,
    )
    logger.info("Patient %s replied %s by SMS.", patient.id, keyword)
    return ReplyResult(
        ReplyOutcome.STOPPED if wanted else ReplyOutcome.RESTARTED, patient.id
    )
