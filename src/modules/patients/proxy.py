"""One phone, a household: acting for a dependant, with consent and a code (Issue 84).

A daughter books for her mother; a parent joins a queue for a child. This module is the whole of what
that means, and the rule it keeps is one sentence: **the ticket belongs to the person being seen.**

* :func:`link_with_code` and :func:`send_link_code` link a **number**: the code goes to that number, and
  only somebody holding the phone can finish the link. That is the verification step — you cannot attach
  yourself to a stranger's record by typing their number.
* :func:`link_without_phone` makes a dependant who has **no phone of their own** (a small child). There
  is no number to prove, and no existing record to take over: the row is created by the proxy, empty,
  and can never sign in.
* :func:`dependants` lists the people a patient may act for; :func:`acting_for` is the one gate every
  action goes through, and it refuses the moment a link is revoked (:func:`revoke`).
* :func:`messages_for` is what the notification service asks: the phone a patient's messages go to. For
  a dependant, that is their proxy's phone, and the proxy's own preferences, quiet hours and opt-out
  apply, because it is the proxy's phone that rings.

**Consent.** The link records ``ConsentPurpose.PROXY_ACTIONS`` against the **dependant**, by the proxy,
at the moment it is made. Revoking withdraws it. The wording (``consent_text.py``) says either of them
can end it and that ending it is immediate.

**The audit trail names both.** Every action through a link is an audit row whose actor is the proxy and
whose entity is the dependant, and the ticket and booking carry ``proxy_patient_id`` as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    ConsentPurpose,
    OtpSubjectKind,
    OtpVerification,
    PatientChannel,
    ProxyRelationship,
)
from src.commons.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.commons.phone import normalize_phone
from src.commons.time import now_sast
from src.core import otp_store
from src.core.audit import record_audit_event
from src.core.config import get_settings
from src.database.models import Patient, PatientLink
from src.modules.patients.consent import record_consent
from src.modules.patients.service import (
    CodeSent,
    OtpRejectedError,
    OtpThrottledError,
    SmsSignInUnavailableError,
    actor_for,
    get_or_create_patient,
    send_code,
)

#: How many people one phone may act for. High enough for a household, low enough that a number
#: collecting dozens of records is a question the clinic gets to ask.
MAX_DEPENDANTS: Final = 8
#: The wire code for a refusal that is about the link itself.
LINK_NOT_FOUND_CODE: Final = "patients.link.not_found"
LINK_REVOKED_CODE: Final = "patients.link.revoked"
LINK_EXISTS_CODE: Final = "patients.link.exists"
LINK_SELF_CODE: Final = "patients.link.self"
LINK_TOO_MANY_CODE: Final = "patients.link.too_many"


class LinkNotFoundError(NotFoundError):
    """No such link for this patient: HTTP 404, the same for somebody else's link."""

    def __init__(self) -> None:
        super().__init__("No such person.", code=LINK_NOT_FOUND_CODE)


class ProxyRefusedError(ForbiddenError):
    """The link exists but may not be used now (it was ended): HTTP 403."""

    def __init__(self) -> None:
        super().__init__(
            "You can no longer act for this person.", code=LINK_REVOKED_CODE
        )


class LinkRefusedError(ConflictError):
    """The link cannot be made: HTTP 409, with which rule refused it."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, code=code)


@dataclass(frozen=True, slots=True)
class Dependant:
    """One person a patient may act for, as their own screens show it."""

    link_id: str
    patient_id: str
    name: str
    relationship: ProxyRelationship
    #: True when the dependant has a phone of their own, proved with a code when the link was made.
    has_phone: bool


def _links(db: Session, proxy_patient_id: str) -> list[PatientLink]:
    """Every live link this patient holds, oldest first."""
    return list(
        db.execute(
            select(PatientLink)
            .where(
                PatientLink.proxy_patient_id == proxy_patient_id,
                PatientLink.revoked_at.is_(None),
            )
            .order_by(PatientLink.created_at)
        ).scalars()
    )


def _named(db: Session, link: PatientLink) -> Dependant:
    """One link as a screen reads it: who they are and how they are related."""
    patient = db.get(Patient, link.dependant_patient_id)
    return Dependant(
        link_id=link.id,
        patient_id=link.dependant_patient_id,
        name=(patient.display_name if patient and patient.display_name else "Someone"),
        relationship=link.relationship_enum,
        has_phone=bool(patient and patient.phone_e164),
    )


def dependants(db: Session, proxy_patient_id: str) -> list[Dependant]:
    """The people this patient may act for, in the order the links were made."""
    return [_named(db, link) for link in _links(db, proxy_patient_id)]


def acting_for(db: Session, proxy: Patient, dependant_patient_id: str) -> Patient:
    """The dependant this patient may act for now: **the one gate** every proxy action goes through.

    Raises:
        LinkNotFoundError: No link, or somebody else's link, or the dependant is gone.
        ProxyRefusedError: The link was ended.
    """
    link = db.execute(
        select(PatientLink).where(
            PatientLink.proxy_patient_id == proxy.id,
            PatientLink.dependant_patient_id == dependant_patient_id,
        )
    ).scalar_one_or_none()
    if link is None:
        raise LinkNotFoundError()
    if link.revoked_at is not None:
        raise ProxyRefusedError()
    patient = db.get(Patient, dependant_patient_id)
    if patient is None or patient.is_deleted:
        raise LinkNotFoundError()
    return patient


def patient_or_dependant(
    db: Session, patient: Patient, for_patient_id: str | None
) -> tuple[Patient, Patient | None]:
    """Who this action is for, and who is acting: ``(the patient seen, the proxy or None)``.

    ``for_patient_id`` is the dependant a proxy names; ``None``, or the caller's own id, means the
    caller is acting for themselves and nothing about the action changes.

    Raises:
        LinkNotFoundError, ProxyRefusedError: As :func:`acting_for`.
    """
    if for_patient_id is None or for_patient_id == patient.id:
        return patient, None
    return acting_for(db, patient, for_patient_id), patient


def _audit(
    db: Session,
    proxy: Patient,
    dependant_patient_id: str,
    what: str,
    *,
    action: AuditAction = AuditAction.UPDATE,
    site_id: str | None = None,
) -> None:
    """One row naming both people. Never a phone number or a name: ids and what happened."""
    record_audit_event(
        db,
        action=action,
        entity_type=AuditEntityType.PATIENT,
        entity_id=dependant_patient_id,
        actor=actor_for(proxy),
        site_id=site_id,
        context=what,
    )


def record_action(
    db: Session, proxy: Patient, dependant: Patient, what: str, *, site_id: str | None
) -> None:
    """Record that a proxy did something for a dependant (a join, a booking, a cancellation)."""
    _audit(db, proxy, dependant.id, f"{what} on behalf", site_id=site_id)


def _guard_new_link(
    db: Session, proxy: Patient, dependant_patient_id: str | None
) -> None:
    """The rules that refuse a link before anything is written."""
    if dependant_patient_id == proxy.id:
        raise LinkRefusedError(
            "That is your own number. You do not need a link to act for yourself.",
            code=LINK_SELF_CODE,
        )
    if len(_links(db, proxy.id)) >= MAX_DEPENDANTS:
        raise LinkRefusedError(
            f"You can act for at most {MAX_DEPENDANTS} people. End a link you no longer need first.",
            code=LINK_TOO_MANY_CODE,
        )
    if dependant_patient_id is not None:
        existing = db.execute(
            select(PatientLink).where(
                PatientLink.proxy_patient_id == proxy.id,
                PatientLink.dependant_patient_id == dependant_patient_id,
                PatientLink.revoked_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise LinkRefusedError(
                "You already act for this person.", code=LINK_EXISTS_CODE
            )


def send_link_code(db: Session, proxy: Patient, *, raw_phone: str, ip: str) -> CodeSent:
    """Send a code to the number a patient wants to act for. The caller commits nothing; this does.

    The same code path as signing in (:func:`~src.modules.patients.service.send_code`), so the same
    budgets, cooldown and store apply, and the message is the one-time-code message the number's owner
    already knows.

    Raises:
        InvalidPhoneNumberError, OtpThrottledError, SmsSignInUnavailableError, UpstreamError: As ``send_code``.
        LinkRefusedError: It is the caller's own number, or they already act for that person, or they
            hold as many links as they may.
    """
    phone = normalize_phone(raw_phone)
    known = db.execute(
        select(Patient).where(
            Patient.phone_e164 == phone, Patient.is_deleted.is_(False)
        )
    ).scalar_one_or_none()
    if known is not None and known.id == proxy.id:
        raise LinkRefusedError(
            "That is your own number. You do not need a link to act for yourself.",
            code=LINK_SELF_CODE,
        )
    _guard_new_link(db, proxy, known.id if known else None)
    return send_code(db, raw_phone=phone, ip=ip)


def link_with_code(
    db: Session,
    proxy: Patient,
    *,
    raw_phone: str,
    code: str,
    relationship: ProxyRelationship,
    name: str | None = None,
    moment: datetime | None = None,
) -> PatientLink:
    """Finish a link to a number, with the code sent to it. The caller commits.

    The code is the whole of the check: whoever answers holds that phone. The dependant's record is
    created on first use, exactly as signing in creates it.

    Raises:
        InvalidPhoneNumberError: The number cannot be read (422).
        OtpRejectedError: Wrong, expired, used or locked (400).
        LinkRefusedError: One of the rules in :func:`_guard_new_link`.
    """
    moment = moment or now_sast()
    phone = normalize_phone(raw_phone)
    outcome = otp_store.check_code(OtpSubjectKind.PHONE, phone, code)
    if outcome is not OtpVerification.VERIFIED:
        raise OtpRejectedError(
            outcome, otp_store.attempts_left(OtpSubjectKind.PHONE, phone)
        )
    dependant, _ = get_or_create_patient(db, phone)
    _guard_new_link(db, proxy, dependant.id)
    dependant.phone_verified_at = dependant.phone_verified_at or moment
    if name and not dependant.display_name:
        dependant.display_name = name.strip()[:80]
    return _make(db, proxy, dependant, relationship, verified_at=moment)


def link_without_phone(
    db: Session,
    proxy: Patient,
    *,
    name: str,
    relationship: ProxyRelationship,
    moment: datetime | None = None,
) -> PatientLink:
    """Make a dependant who has no phone of their own — a small child. The caller commits.

    Nothing is verified because there is nothing to verify: this creates a new, empty record that
    nobody can sign in as. It is the proxy's phone that carries every message about them.

    Raises:
        LinkRefusedError: The caller already holds as many links as they may.
    """
    moment = moment or now_sast()
    _guard_new_link(db, proxy, None)
    dependant = Patient(phone_e164=None, display_name=name.strip()[:80])
    db.add(dependant)
    db.flush()
    return _make(db, proxy, dependant, relationship, verified_at=None)


def _make(
    db: Session,
    proxy: Patient,
    dependant: Patient,
    relationship: ProxyRelationship,
    *,
    verified_at: datetime | None,
) -> PatientLink:
    """Write the link, the dependant's consent to it, and the audit row naming both people."""
    link = PatientLink(
        proxy_patient_id=proxy.id,
        dependant_patient_id=dependant.id,
        relationship_kind=relationship.value,
        verified_at=verified_at,
    )
    db.add(link)
    db.flush()
    record_consent(
        db,
        dependant,
        ConsentPurpose.PROXY_ACTIONS,
        granted=True,
        channel=PatientChannel.WEB,
        actor=actor_for(proxy),
    )
    _audit(
        db,
        proxy,
        dependant.id,
        f"link made: acts for this patient as their {relationship.value}"
        + (" (number proved by code)" if verified_at else " (no phone of their own)"),
        action=AuditAction.CREATE,
    )
    return link


def revoke(
    db: Session, patient: Patient, link_id: str, *, moment: datetime | None = None
) -> PatientLink:
    """End a link, from either side. The caller commits.

    The proxy stops being able to act the moment this commits: :func:`acting_for` refuses, and the
    dependant's consent to being acted for is withdrawn. Tickets already issued are untouched — they
    belong to the dependant, and the audit trail still says who took them.

    Raises:
        LinkNotFoundError: No such link, or one that is neither side's.
    """
    moment = moment or now_sast()
    link = db.execute(
        select(PatientLink).where(
            PatientLink.id == link_id,
            or_(
                PatientLink.proxy_patient_id == patient.id,
                PatientLink.dependant_patient_id == patient.id,
            ),
        )
    ).scalar_one_or_none()
    if link is None:
        raise LinkNotFoundError()
    if link.revoked_at is not None:
        return link
    link.revoked_at = moment
    link.revoked_by = patient.id
    db.flush()
    dependant = db.get(Patient, link.dependant_patient_id)
    if dependant is not None:
        record_consent(
            db,
            dependant,
            ConsentPurpose.PROXY_ACTIONS,
            granted=False,
            channel=PatientChannel.WEB,
            actor=actor_for(patient),
        )
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.PATIENT,
        entity_id=link.dependant_patient_id,
        actor=actor_for(patient),
        context="link ended: nobody acts for this patient through it any more",
    )
    return link


def messages_for(db: Session, patient: Patient) -> Patient:
    """Whose phone a patient's messages go to: their own, or their proxy's.

    The notification service asks this before it chooses a transport, so a dependant's ticket messages
    reach the phone that exists, under that phone owner's own preferences and opt-out. A patient with
    their own number is always their own answer; only a dependant is redirected, and only while the
    link is live. Two proxies is not a case: the oldest live link answers.
    """
    if patient.phone_e164 is not None or patient.is_deleted:
        return patient
    link = (
        db.execute(
            select(PatientLink)
            .where(
                PatientLink.dependant_patient_id == patient.id,
                PatientLink.revoked_at.is_(None),
            )
            .order_by(PatientLink.created_at)
        )
        .scalars()
        .first()
    )
    if link is None:
        return patient
    proxy = db.get(Patient, link.proxy_patient_id)
    return proxy if proxy is not None and not proxy.is_deleted else patient


def sms_available() -> bool:
    """Whether a link to a number can be started at all (the code goes by SMS)."""
    return bool(get_settings().sms_enabled)


__all__ = [
    "MAX_DEPENDANTS",
    "Dependant",
    "LinkNotFoundError",
    "LinkRefusedError",
    "OtpRejectedError",
    "OtpThrottledError",
    "ProxyRefusedError",
    "SmsSignInUnavailableError",
    "acting_for",
    "dependants",
    "link_with_code",
    "link_without_phone",
    "messages_for",
    "patient_or_dependant",
    "record_action",
    "revoke",
    "send_link_code",
    "sms_available",
]
