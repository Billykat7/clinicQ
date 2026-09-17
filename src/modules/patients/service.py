"""Patient identity: one contact, one patient; codes by SMS or email; channels that vouch (Issue 17).

Everything that touches the database or the OTP store, and nothing that touches HTTP. The router
maps the three exceptions below to 429, 400 and 502.

A patient is a **contact a one-time code can reach**. There are two, and everything below is
written twice on purpose — once per contact, over one shared set of helpers:

* a **mobile number**, proved by an SMS (Issue 17), and the only one a USSD or WhatsApp gateway
  can vouch for;
* an **email address**, proved by an emailed code (Issue 219), offered only where
  ``PATIENT_EMAIL_SIGN_IN_ENABLED`` is on.

They are peers. The same OTP store keyed by ``(kind, identifier)``, the same cooldown and budgets,
the same outcomes and the same "no patient is created until a code is verified"; a patient created
either way joins queues, holds tickets and opens ``/t/`` identically. A patient may hold one contact
or both, and each is unique, so one contact is always one patient.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.email_address import normalize_email
from src.commons.enums import (
    GATEWAY_TRUSTED_CHANNELS,
    AuditAction,
    AuditEntityType,
    BoundedContext,
    NotificationStatus,
    NotificationTemplate,
    OtpSubjectKind,
    OtpVerification,
    PatientChannel,
)
from src.commons.exceptions import BKPropertyError, UpstreamError
from src.commons.phone import normalize_phone
from src.commons.schemas import ModuleInfo
from src.commons.time import now_sast
from src.core import otp_store
from src.core.audit import record_audit_event
from src.core.config import get_settings
from src.core.email_send import EmailDeliveryError, send_otp_email
from src.database.models import Patient
from src.modules.notifications import service as notifications
from src.modules.notifications.sms import SmsProvider

#: How a patient acting on their own record is named in the audit trail (never their number).
PATIENT_ACTOR_PREFIX = "patient"


def get_module_info() -> ModuleInfo:
    """Return this module's metadata for its ``/info`` endpoint."""
    return ModuleInfo(
        context=BoundedContext.PATIENTS,
        summary="Patients: a phone number proved by a one-time code; no password on any channel.",
    )


def actor_for(patient: Patient) -> str:
    """The audit actor label for a patient: ``patient:<id>``."""
    return f"{PATIENT_ACTOR_PREFIX}:{patient.id}"


class SmsSignInUnavailableError(BKPropertyError):
    """SMS is switched off for this deployment (``SMS_ENABLED``), so no sign-in code can be sent: HTTP 503."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(
            "Sign-in by SMS code is not available here. Please try again later or ask at the clinic.",
            code="patients.otp.sms_disabled",
        )


class EmailSignInUnavailableError(BKPropertyError):
    """Email sign-in is switched off for this deployment (``PATIENT_EMAIL_SIGN_IN_ENABLED``): HTTP 503.

    The twin of :class:`SmsSignInUnavailableError`, and answered the same way, so a client that
    already handles "that way in is not available here" needs no new branch. It is a 503 rather
    than a 404 because the route exists and the deployment has switched the method off, which is
    exactly what 503 means.
    """

    status_code = HTTPStatus.SERVICE_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(
            "Signing in with an email address is not available here. Use your mobile number.",
            code="patients.otp.email_disabled",
        )


class OtpThrottledError(Exception):
    """A code was requested too soon, or too often: the router answers 429 with ``Retry-After``."""

    def __init__(self, retry_after_seconds: int) -> None:
        """Keep how long the caller must wait."""
        super().__init__("Too many code requests. Try again shortly.")
        self.retry_after_seconds = retry_after_seconds


class OtpRejectedError(Exception):
    """The code was wrong, expired, used or locked: the router answers 400 with the outcome."""

    def __init__(self, outcome: OtpVerification, attempts_left: int) -> None:
        """Keep the outcome and the attempts the current code has left."""
        super().__init__(outcome.value)
        self.outcome = outcome
        self.attempts_left = attempts_left


@dataclass(frozen=True, slots=True)
class CodeSent:
    """What the caller may tell the patient about the code just sent."""

    expires_in_seconds: int
    resend_after_seconds: int


# --------------------------------------------------------------------------------------
# What both contacts share: the budget, the cooldown, and how a code is checked
#
# Written once and called twice, so the number and the address cannot drift apart. Every one of
# these takes the :class:`~src.commons.enums.OtpSubjectKind` it is acting for, which is what keys
# the store, so a code issued for an address can never be spent on a number.
# --------------------------------------------------------------------------------------


def _reserve_code(kind: OtpSubjectKind, identifier: str, ip: str) -> str:
    """Check the budget and the cooldown, then issue a code. The caller sends it.

    Raises:
        OtpThrottledError: Over the per-identifier or per-IP budget, or inside the resend cooldown.
    """
    settings = get_settings()
    if not otp_store.within_request_limit(kind, identifier, ip):
        raise OtpThrottledError(settings.otp_rate_limit_window_minutes * 60)
    wait = otp_store.resend_wait_seconds(kind, identifier)
    if wait:
        raise OtpThrottledError(wait)
    return otp_store.issue_code(kind, identifier)


def _code_sent(kind: OtpSubjectKind, identifier: str, ip: str) -> CodeSent:
    """Count the request that just went out, and say what the patient may be told."""
    settings = get_settings()
    otp_store.record_request(kind, identifier, ip)
    return CodeSent(
        expires_in_seconds=settings.otp_ttl_minutes * 60,
        resend_after_seconds=settings.otp_resend_cooldown_seconds,
    )


def _spend_code(kind: OtpSubjectKind, identifier: str, code: str) -> None:
    """Check the code, or raise. On success it is spent and cannot be used again.

    Raises:
        OtpRejectedError: Wrong, expired, used or locked (400).
    """
    outcome = otp_store.check_code(kind, identifier, code)
    if outcome is not OtpVerification.VERIFIED:
        raise OtpRejectedError(outcome, otp_store.attempts_left(kind, identifier))


# --------------------------------------------------------------------------------------
# One number, one patient
# --------------------------------------------------------------------------------------


def find_patient(db: Session, raw_phone: str) -> Patient | None:
    """The patient a number belongs to, however it is written, or None."""
    phone = normalize_phone(raw_phone)
    return db.execute(
        select(Patient).where(Patient.phone_e164 == phone)
    ).scalar_one_or_none()


def get_or_create_patient(db: Session, phone_e164: str) -> tuple[Patient, bool]:
    """Return ``(patient, created)`` for an already-normalised number. Never makes a duplicate.

    Two sign-ins for a new number can race; the unique constraint on ``phone_e164`` lets exactly
    one insert win, and the loser reads the winner's row inside its own savepoint, so its
    transaction survives. A soft-deleted patient comes back as a fresh record: nothing they shared
    before (a name, a WhatsApp id) is restored.
    """
    existing = db.execute(
        select(Patient).where(Patient.phone_e164 == phone_e164)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.is_deleted:
            existing.is_deleted = False
            existing.display_name = None
            existing.whatsapp_id = None
        return existing, False
    patient = Patient(phone_e164=phone_e164)
    try:
        with db.begin_nested():
            db.add(patient)
    except IntegrityError:
        winner = db.execute(
            select(Patient).where(Patient.phone_e164 == phone_e164)
        ).scalar_one()
        return winner, False
    return patient, True


def _touch(patient: Patient, channel: PatientChannel) -> None:
    """Record the channel and the time of the patient's latest visit."""
    patient.last_channel = channel.value
    patient.last_seen_at = now_sast()


def _audit_created(
    db: Session, patient: Patient, *, actor: str, channel: PatientChannel
) -> None:
    """One audit row for a new patient: no number, no name, only the channel it came through."""
    record_audit_event(
        db,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.PATIENT,
        entity_id=patient.id,
        actor=actor,
        context=f"patient record created on {channel.value}",
    )


# --------------------------------------------------------------------------------------
# The web: a code by SMS
# --------------------------------------------------------------------------------------


def send_code(
    db: Session, *, raw_phone: str, ip: str, provider: SmsProvider | None = None
) -> CodeSent:
    """Send a sign-in code to a number. No patient is created until the code is verified.

    Raises:
        SmsSignInUnavailableError: SMS is switched off (``SMS_ENABLED``): nothing is issued or counted (503).
        InvalidPhoneNumberError: The number cannot be normalised (422).
        OtpThrottledError: Over the per-number or per-IP budget, or inside the resend cooldown.
        UpstreamError: The SMS could not be handed to the provider (502); the code is discarded.
    """
    settings = get_settings()
    if not settings.sms_enabled:
        raise SmsSignInUnavailableError()
    phone = normalize_phone(raw_phone)
    code = _reserve_code(OtpSubjectKind.PHONE, phone, ip)
    notification = notifications.send_sms(
        db,
        to=phone,
        template=NotificationTemplate.OTP_SIGN_IN,
        context={"code": code, "ttl_minutes": settings.otp_ttl_minutes},
        provider=provider,
    )
    db.commit()
    if notification.status != NotificationStatus.SENT.value:
        otp_store.discard_code(OtpSubjectKind.PHONE, phone)
        raise UpstreamError(
            "The code could not be sent. Try again in a minute.",
            code="patients.otp.not_sent",
        )
    return _code_sent(OtpSubjectKind.PHONE, phone, ip)


def verify_code(db: Session, *, raw_phone: str, code: str) -> Patient:
    """Check the code; on success return the number's patient, created on first verification.

    Raises:
        InvalidPhoneNumberError: The number cannot be normalised (422).
        OtpRejectedError: Wrong, expired, used or locked (400).
    """
    phone = normalize_phone(raw_phone)
    _spend_code(OtpSubjectKind.PHONE, phone, code)
    patient, created = get_or_create_patient(db, phone)
    patient.phone_verified_at = now_sast()
    _touch(patient, PatientChannel.WEB)
    db.flush()
    if created:
        _audit_created(
            db, patient, actor=actor_for(patient), channel=PatientChannel.WEB
        )
    db.commit()
    return patient


# --------------------------------------------------------------------------------------
# The web: a code by email (Issue 219)
#
# The phone path above, contact for contact. What is *not* different is the point: the same store,
# the same budgets and cooldown, the same outcomes, the same "no patient until a code is verified",
# and a patient at the end of it who is a patient like any other.
# --------------------------------------------------------------------------------------


def find_patient_by_email(db: Session, raw_email: str) -> Patient | None:
    """The patient an address belongs to, however it is capitalised, or None."""
    return db.execute(
        select(Patient).where(Patient.email == normalize_email(raw_email))
    ).scalar_one_or_none()


def get_or_create_patient_by_email(db: Session, email: str) -> tuple[Patient, bool]:
    """Return ``(patient, created)`` for an already-normalised address. Never makes a duplicate.

    The twin of :func:`get_or_create_patient`, including the race: two first sign-ins at one
    address can arrive together, the unique constraint lets exactly one insert win, and the loser
    reads the winner's row inside its own savepoint so its transaction survives. A soft-deleted
    patient comes back as a fresh record: nothing they shared before is restored.

    The patient it creates has **no phone number**, which is allowed (the column is nullable since
    Issue 84) and is the whole point: someone whose number changed, or who has no mobile, is still
    a patient here. Until Issue 220 lands they cannot be reached by the notification chain, which
    is why ``PATIENT_EMAIL_SIGN_IN_ENABLED`` is off by default.
    """
    existing = db.execute(
        select(Patient).where(Patient.email == email)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.is_deleted:
            existing.is_deleted = False
            existing.display_name = None
            existing.whatsapp_id = None
        return existing, False
    patient = Patient(email=email)
    try:
        with db.begin_nested():
            db.add(patient)
    except IntegrityError:
        winner = db.execute(select(Patient).where(Patient.email == email)).scalar_one()
        return winner, False
    return patient, True


def send_email_code(db: Session, *, raw_email: str, ip: str) -> CodeSent:
    """Send a sign-in code to an address. No patient is created until the code is verified.

    Raises:
        EmailSignInUnavailableError: The flag is off: nothing is issued or counted (503).
        InvalidEmailAddressError: The address cannot be normalised (422).
        OtpThrottledError: Over the per-address or per-IP budget, or inside the resend cooldown.
        UpstreamError: The email could not be handed to SMTP (502); the code is discarded, exactly
            as an SMS the provider refused discards its own.
    """
    if not get_settings().patient_email_sign_in_enabled:
        raise EmailSignInUnavailableError()
    email = normalize_email(raw_email)
    code = _reserve_code(OtpSubjectKind.EMAIL, email, ip)
    try:
        send_otp_email(email, code, client_ip=ip)
    except EmailDeliveryError as exc:
        otp_store.discard_code(OtpSubjectKind.EMAIL, email)
        raise UpstreamError(
            "The code could not be sent. Try again in a minute.",
            code="patients.otp.not_sent",
        ) from exc
    return _code_sent(OtpSubjectKind.EMAIL, email, ip)


def verify_email_code(db: Session, *, raw_email: str, code: str) -> Patient:
    """Check the code; on success return the address's patient, created on first verification.

    Raises:
        EmailSignInUnavailableError: The flag is off (503). Checked here too, so switching the flag
            off ends a sign-in half-way through it rather than letting an issued code finish.
        InvalidEmailAddressError: The address cannot be normalised (422).
        OtpRejectedError: Wrong, expired, used or locked (400).
    """
    if not get_settings().patient_email_sign_in_enabled:
        raise EmailSignInUnavailableError()
    email = normalize_email(raw_email)
    _spend_code(OtpSubjectKind.EMAIL, email, code)
    patient, created = get_or_create_patient_by_email(db, email)
    patient.email_verified_at = now_sast()
    _touch(patient, PatientChannel.WEB)
    db.flush()
    if created:
        _audit_created(
            db, patient, actor=actor_for(patient), channel=PatientChannel.WEB
        )
    db.commit()
    return patient


# --------------------------------------------------------------------------------------
# USSD and WhatsApp: the gateway vouches for the number (see the package docstring)
# --------------------------------------------------------------------------------------


def patient_for_gateway(
    db: Session,
    *,
    msisdn: str,
    channel: PatientChannel,
    whatsapp_id: str | None = None,
) -> Patient:
    """Resolve (or create) the patient for a number a trusted gateway vouched for. No code.

    For the USSD and WhatsApp handlers (Issues 73, 75), which must authenticate the gateway before
    calling this. It never issues a web session. The caller commits.

    Raises:
        ValueError: ``channel`` is not a gateway-trusted channel (the web and walk-in are not).
        InvalidPhoneNumberError: The MSISDN cannot be normalised.
    """
    if channel not in GATEWAY_TRUSTED_CHANNELS:
        raise ValueError(f"{channel.value} does not vouch for a caller's number")
    patient, created = get_or_create_patient(db, normalize_phone(msisdn))
    if channel is PatientChannel.WHATSAPP and whatsapp_id:
        patient.whatsapp_id = whatsapp_id
    _touch(patient, channel)
    db.flush()
    if created:
        _audit_created(db, patient, actor=f"{channel.value}-gateway", channel=channel)
    return patient


def update_display_name(
    db: Session, patient: Patient, display_name: str | None
) -> Patient:
    """Change what the patient asked to be called; audited without the name itself. Commits."""
    cleaned = (display_name or "").strip() or None
    if cleaned != patient.display_name:
        patient.display_name = cleaned
        record_audit_event(
            db,
            action=AuditAction.UPDATE,
            entity_type=AuditEntityType.PATIENT,
            entity_id=patient.id,
            actor=actor_for(patient),
            context="display name changed",
        )
    db.commit()
    return patient
