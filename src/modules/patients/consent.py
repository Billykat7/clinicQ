"""Consent: may we, for this patient, for this purpose (Issue 21).

Two rules make this small module load-bearing:

1. **:func:`has_consent` is the only answer**, and the two surfaces that could expose a patient
   both go through it: the notification service asks it in
   :func:`src.modules.notifications.preferences.resolve`, which every send path already funnels
   through, and the board asks it through :func:`board_projection`, which is the only function that
   turns a patient into something a public screen may show.
   ``tests/unit/security/test_consent_is_not_bypassable.py`` fails the build if either stops.
2. **No row means no.** A purpose a patient has never answered is not granted, on every channel, so
   the most private option is the default everywhere without anyone setting it.

Withdrawal is an ordinary answer with ``granted`` false. It takes effect at once: the current row is
what both surfaces read, and the history row beside it is what proves the patient decided.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    AuditAction,
    AuditEntityType,
    ConsentPurpose,
    DisplayMode,
    NotificationTemplate,
    PatientChannel,
)
from src.commons.time import business_date, now_sast
from src.core.audit import record_audit_event
from src.core.domain_events import QueueChanged, publish_after_commit
from src.database.models import Patient, PatientConsent, PatientConsentEvent
from src.modules.patients.consent_text import CONSENT_WORDING_VERSION

#: The one message a patient never has to agree to receive: the code they just asked for by typing
#: their number. Everything else to a patient needs :attr:`ConsentPurpose.NOTIFICATIONS`.
CONSENT_EXEMPT_TEMPLATES: frozenset[NotificationTemplate] = frozenset(
    {NotificationTemplate.OTP_SIGN_IN}
)


def consent_required_for(template: NotificationTemplate) -> ConsentPurpose | None:
    """The purpose a message needs consent for, or ``None`` when the patient asked for it.

    A sign-in code is the service the patient requested by typing their number (POPIA s11(1)(b):
    performance of what they asked for), so it is not gated by a consent they would have to give
    before they could sign in to give it. Every other message to a patient is.
    """
    if template in CONSENT_EXEMPT_TEMPLATES:
        return None
    return ConsentPurpose.NOTIFICATIONS


def has_consent(db: Session, patient_id: str, purpose: ConsentPurpose) -> bool:
    """Whether this patient has said yes to this purpose, right now.

    ``False`` when they said no, when they withdrew, and when they were never asked — the absence of
    an answer is never taken as one.
    """
    granted = db.execute(
        select(PatientConsent.granted).where(
            PatientConsent.patient_id == patient_id,
            PatientConsent.purpose == purpose.value,
        )
    ).scalar_one_or_none()
    return bool(granted)


def consent_state(db: Session, patient_id: str) -> dict[ConsentPurpose, bool]:
    """Every purpose and its current answer, with the unanswered ones as ``False``."""
    rows = db.execute(
        select(PatientConsent.purpose, PatientConsent.granted).where(
            PatientConsent.patient_id == patient_id
        )
    ).all()
    answered = {purpose: bool(granted) for purpose, granted in rows}
    return {purpose: answered.get(purpose.value, False) for purpose in ConsentPurpose}


def record_consent(
    db: Session,
    patient: Patient,
    purpose: ConsentPurpose,
    *,
    granted: bool,
    channel: PatientChannel,
    recorded_by: str | None = None,
    site_id: str | None = None,
    wording_version: str = CONSENT_WORDING_VERSION,
) -> PatientConsent:
    """Record one answer: the history row that proves it, and the current row both surfaces read.

    The caller commits. An answer is recorded even when it repeats the current one, because
    "asked again on another channel and said the same" is itself a fact worth keeping.

    Args:
        db: The session.
        patient: Whose answer this is.
        purpose: What they were asked.
        granted: What they said. ``False`` is a withdrawal (or a refusal).
        channel: Where they answered: their own device, the USSD menu, WhatsApp, or the desk.
        recorded_by: The staff member who recorded it, when it was given at the desk.
        site_id: The clinic they answered at, when they answered at one.
        wording_version: Which wording they were shown; defaults to the current one.
    """
    db.add(
        PatientConsentEvent(
            patient_id=patient.id,
            purpose=purpose.value,
            granted=granted,
            source_channel=channel.value,
            wording_version=wording_version,
            recorded_by=recorded_by,
            site_id=site_id,
        )
    )
    current = db.execute(
        select(PatientConsent).where(
            PatientConsent.patient_id == patient.id,
            PatientConsent.purpose == purpose.value,
        )
    ).scalar_one_or_none()
    if current is None:
        current = PatientConsent(
            patient_id=patient.id,
            purpose=purpose.value,
            granted=granted,
            source_channel=channel.value,
            wording_version=wording_version,
        )
        db.add(current)
    else:
        current.granted = granted
        current.source_channel = channel.value
        current.wording_version = wording_version
        current.decided_at = now_sast()
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.PATIENT_CONSENT,
        entity_id=patient.id,
        actor=f"patient:{patient.id}" if recorded_by is None else recorded_by,
        actor_id=recorded_by,
        site_id=site_id,
        # The purpose and the answer, never the patient's details: what changed, not who they are.
        context=f"{purpose.value}={'granted' if granted else 'withdrawn'} via {channel.value}",
    )
    db.flush()
    if purpose in BOARD_PURPOSES:
        _announce_to_boards(db, patient.id)
    return current


#: The answers a waiting-room board reads: a change to either redraws the patient's boards (Issue 57).
BOARD_PURPOSES: frozenset[ConsentPurpose] = frozenset(
    {ConsentPurpose.DISPLAY_NAME, ConsentPurpose.DISPLAY_COMMENT}
)


def _announce_to_boards(db: Session, patient_id: str) -> None:
    """Tell the boards showing this patient today that what they may show changed, after the commit.

    The board reads consent afresh every time it projects (Issue 58), so all a withdrawal needs is for
    the boards to project again: one ``QueueChanged`` per queue the patient is still in today, which the
    live streams turn into a new board. Nothing about the patient travels with it.
    """
    from src.modules.queue.tickets import (
        patient_tickets_select,  # the queue module imports this one
    )

    active = {status.value for status in TICKET_ACTIVE_STATUSES}
    for ticket in db.execute(
        patient_tickets_select(patient_id, business_date())
    ).scalars():
        if ticket.status in active:
            publish_after_commit(
                db, QueueChanged(site_id=ticket.site_id, queue_id=ticket.queue_id)
            )


# --------------------------------------------------------------------------------------
# The board's one projection (Issue 58 renders it; this decides what it may render)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoardEntry:
    """What the waiting-room board may show for one ticket. Never a patient object.

    Built only by :func:`board_projection`, so a template cannot be handed a patient and decide for
    itself. ``name`` and ``comment`` are ``None`` unless both the site's display mode and the
    patient's consent allow them, which is non-negotiable 4 in one place.
    """

    ticket_number: str
    name: str | None = None
    comment: str | None = None


def _short_name(display_name: str) -> str:
    """``Thabo Mokoena`` → ``Thabo M.``: the name-lite form (Issue 27's middle display mode)."""
    first, _, last = display_name.strip().partition(" ")
    return f"{first} {last[0]}." if last else first


def project_entry(
    *,
    ticket_number: str,
    display_mode: DisplayMode,
    display_name: str | None,
    comment: str | None,
    name_consented: bool,
    comment_consented: bool,
) -> BoardEntry:
    """The display rule itself, given the answers :func:`board_projection` reads (Issue 54).

    Pure, so the settings screen's live preview renders a sample row through the very rule the
    board uses instead of a copy of it: the mode decides whether a name may appear and in which
    form, consent decides whether this person's name and reason may, and only a full-name mode ever
    carries a reason. :func:`board_projection` is the only caller that reads real consent.
    """
    if (
        display_mode is DisplayMode.NUMBER_ONLY
        or not display_name
        or not name_consented
    ):
        return BoardEntry(ticket_number=ticket_number)
    name = (
        display_name if display_mode is DisplayMode.FULL else _short_name(display_name)
    )
    shown_comment = (
        comment
        if comment and display_mode is DisplayMode.FULL and comment_consented
        else None
    )
    return BoardEntry(ticket_number=ticket_number, name=name, comment=shown_comment)


def board_projection(
    db: Session,
    patient: Patient | None,
    *,
    ticket_number: str,
    display_mode: DisplayMode,
    comment: str | None = None,
    visit_comment_consent: bool = False,
) -> BoardEntry:
    """What the board may show for this ticket: the number, and only what has been agreed to.

    Three gates, all applied here so no caller can apply one and forget another:

    * the **site's display mode** (Issue 27): under ``NUMBER_ONLY`` a name is not in the result at
      all, so it cannot be hidden with CSS and cannot leak through a template;
    * the **patient's standing consent** (:attr:`ConsentPurpose.DISPLAY_NAME`, and separately
      :attr:`ConsentPurpose.DISPLAY_COMMENT` for the reason), read fresh through
      :func:`has_consent`, so a withdrawal takes effect on the next render;
    * the **per-visit consent** for the reason (``visit_comment_consent``, the ticket's
      ``comment_consent``, Issue 58): a reason beside a full name needs the patient to have agreed
      *for this visit*, not only once, because a reason is a new piece of health information each
      time (non-negotiable 4).

    A walk-in with no patient record shows a number, which is what the default mode shows anyway.
    Consent is only read when the mode could show something, so ``NUMBER_ONLY`` costs no query.
    """
    if patient is None or display_mode is DisplayMode.NUMBER_ONLY:
        return BoardEntry(ticket_number=ticket_number)
    name_consented = bool(patient.display_name) and has_consent(
        db, patient.id, ConsentPurpose.DISPLAY_NAME
    )
    return project_entry(
        ticket_number=ticket_number,
        display_mode=display_mode,
        display_name=patient.display_name,
        comment=comment,
        name_consented=name_consented,
        comment_consented=bool(
            name_consented
            and comment
            and visit_comment_consent
            and display_mode is DisplayMode.FULL
            and has_consent(db, patient.id, ConsentPurpose.DISPLAY_COMMENT)
        ),
    )


@dataclass(frozen=True, slots=True)
class PreviewSample:
    """A made-up patient for the settings preview: what they gave, and what they agreed to."""

    ticket_number: str
    display_name: str | None
    comment: str | None
    name_consented: bool
    comment_consented: bool


#: The display-settings preview's patients (Issue 54). Made up, and chosen to show each case: a name
#: and reason both agreed to, a name agreed to without the reason, a patient who agreed to neither,
#: and a walk-in with no record. Here, beside the rule, because outside this module nothing may read
#: a patient's name (``tests/unit/security/test_consent_is_not_bypassable.py``).
PREVIEW_SAMPLES: tuple[PreviewSample, ...] = (
    PreviewSample("T012", "Thandiwe Mokoena", "Chest pain", True, True),
    PreviewSample("T013", "Sipho Dlamini", "Repeat prescription", True, False),
    PreviewSample("T014", "Lerato Khumalo", "Rash", False, False),
    PreviewSample("P015", None, None, False, False),
)


def display_preview(show_comment: bool) -> dict[str, list[BoardEntry]]:
    """``{mode: entries}``: the sample patients as the board would show them under each mode.

    Every mode at once, through :func:`project_entry`, the board's own rule, so the settings screen
    can switch between them as the manager changes the form without a request and without a copy of
    the rule in the browser. ``show_comment`` is the clinic's "also show the reason" switch, applied
    on top the way the saved setting will be.
    """
    return {
        mode.value: [
            project_entry(
                ticket_number=sample.ticket_number,
                display_mode=mode,
                display_name=sample.display_name,
                comment=sample.comment if show_comment else None,
                name_consented=sample.name_consented,
                comment_consented=sample.comment_consented,
            )
            for sample in PREVIEW_SAMPLES
        ]
        for mode in DisplayMode
    }
