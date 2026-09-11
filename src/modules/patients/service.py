"""Patient identity: one number, one patient; codes by SMS; channels that vouch (Issue 17).

Everything that touches the database or the OTP store, and nothing that touches HTTP. The router
maps the three exceptions below to 429, 400 and 502.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
from src.commons.exceptions import UpstreamError
from src.commons.phone import normalize_phone
from src.commons.schemas import ModuleInfo
from src.commons.time import now_sast
from src.core import otp_store
from src.core.audit import record_audit_event
from src.core.config import get_settings
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
        InvalidPhoneNumberError: The number cannot be normalised (422).
        OtpThrottledError: Over the per-number or per-IP budget, or inside the resend cooldown.
        UpstreamError: The SMS could not be handed to the provider (502); the code is discarded.
    """
    settings = get_settings()
    phone = normalize_phone(raw_phone)
    if not otp_store.within_request_limit(OtpSubjectKind.PHONE, phone, ip):
        raise OtpThrottledError(settings.otp_rate_limit_window_minutes * 60)
    wait = otp_store.resend_wait_seconds(OtpSubjectKind.PHONE, phone)
    if wait:
        raise OtpThrottledError(wait)

    code = otp_store.issue_code(OtpSubjectKind.PHONE, phone)
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
    otp_store.record_request(OtpSubjectKind.PHONE, phone, ip)
    return CodeSent(
        expires_in_seconds=settings.otp_ttl_minutes * 60,
        resend_after_seconds=settings.otp_resend_cooldown_seconds,
    )


def verify_code(db: Session, *, raw_phone: str, code: str) -> Patient:
    """Check the code; on success return the number's patient, created on first verification.

    Raises:
        InvalidPhoneNumberError: The number cannot be normalised (422).
        OtpRejectedError: Wrong, expired, used or locked (400).
    """
    phone = normalize_phone(raw_phone)
    outcome = otp_store.check_code(OtpSubjectKind.PHONE, phone, code)
    if outcome is not OtpVerification.VERIFIED:
        raise OtpRejectedError(
            outcome, otp_store.attempts_left(OtpSubjectKind.PHONE, phone)
        )
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
