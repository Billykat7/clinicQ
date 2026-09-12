"""HTTP routes for patient identity (Issue 17): a code by SMS, a session, the patient's own record.

``/otp/request`` and ``/otp/verify`` are public: they are how a patient signs in. ``/me`` and
``/logout`` take the patient's own session through the ``patient`` role's grant on
``patients.self`` (:func:`src.api.rbac_deps.require_patient`, Issue 18) and act only on the record
the session names, so they carry no id a caller could change.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from src.api.rbac_deps import require_patient
from src.commons.enums import ConsentPurpose, OtpVerification, PatientChannel
from src.commons.phone import mask_phone
from src.core.client_ip import client_ip_or_unknown
from src.core.config import Settings, get_settings
from src.database.models import Patient
from src.database.session import get_db
from src.modules.patients import consent as consent_service
from src.modules.patients import service
from src.modules.patients.consent_text import (
    CONSENT_INTRO,
    CONSENT_WITHDRAWN_NOTICE,
    CONSENT_WORDING,
    CONSENT_WORDING_VERSION,
)
from src.modules.patients.schemas import (
    ConsentAnswerOut,
    ConsentStateOut,
    ConsentUpdateIn,
    OtpRequestIn,
    OtpRequestOut,
    OtpVerifyIn,
    PatientOut,
    PatientUpdateIn,
)
from src.modules.patients.sessions import (
    end_session,
    sign_out_everywhere,
    start_session,
)

router = APIRouter(prefix="/patients", tags=["patients"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

#: The patient's own record, gated by the ``patient`` role's grant on ``patients.self`` (Issue 18).
OwnRecordRead = Annotated[Patient, Depends(require_patient("patients.self", "read"))]
OwnRecordUpdate = Annotated[
    Patient, Depends(require_patient("patients.self", "update"))
]

#: What a patient is told for each refused code. Plain words; nothing about the number.
_REJECTED: dict[OtpVerification, str] = {
    OtpVerification.INVALID: "That code is not right.",
    OtpVerification.EXPIRED: "That code has expired or was already used. Ask for a new one.",
    OtpVerification.LOCKED: "Too many wrong codes. Ask for a new one.",
}


def _out(patient: Patient) -> PatientOut:
    """The patient as their own session sees them, number masked."""
    return PatientOut(
        id=patient.id,
        phone=mask_phone(patient.phone_e164),
        display_name=patient.display_name,
        phone_verified_at=patient.phone_verified_at,
        created_at=patient.created_at,
    )


@router.get("/info", summary="Module metadata", operation_id="patientsInfo")
def patients_info() -> dict[str, str]:
    """Return patients module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


@router.post(
    "/otp/request",
    response_model=OtpRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="patientsOtpRequest",
)
def request_code(body: OtpRequestIn, request: Request, db: DbSession) -> OtpRequestOut:
    """Send a 6-digit sign-in code to a mobile number by SMS.

    The same answer whether or not the number already belongs to a patient; no patient is created
    until the code is verified. 422 for a number that cannot be read, 429 (with ``Retry-After``)
    inside the resend cooldown or over the request budget.
    """
    try:
        sent = service.send_code(
            db, raw_phone=body.phone, ip=client_ip_or_unknown(request)
        )
    except service.OtpThrottledError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    return OtpRequestOut(
        expires_in_seconds=sent.expires_in_seconds,
        resend_after_seconds=sent.resend_after_seconds,
    )


@router.post("/otp/verify", response_model=PatientOut, operation_id="patientsOtpVerify")
def verify_code(
    body: OtpVerifyIn, db: DbSession, settings: SettingsDep
) -> JSONResponse:
    """Verify the code and sign the patient in: their record (created on first verification), and
    an httpOnly session cookie. 400 for a wrong, expired, used or locked code."""
    try:
        patient = service.verify_code(db, raw_phone=body.phone, code=body.code)
    except service.OtpRejectedError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "detail": _REJECTED[exc.outcome],
                "code": f"patients.otp.{exc.outcome.value}",
                "attempts_left": exc.attempts_left,
            },
        )
    response = JSONResponse(content=_out(patient).model_dump(mode="json"))
    start_session(response, patient, settings)
    return response


@router.get("/me", response_model=PatientOut, operation_id="patientsMe")
def get_me(patient: OwnRecordRead) -> PatientOut:
    """The signed-in patient's own record."""
    return _out(patient)


@router.patch("/me", response_model=PatientOut, operation_id="patientsUpdateMe")
def update_me(
    body: PatientUpdateIn, patient: OwnRecordUpdate, db: DbSession
) -> PatientOut:
    """Change what the signed-in patient is called. Any other field is refused (422)."""
    return _out(service.update_display_name(db, patient, body.display_name))


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="patientsLogout"
)
def logout(patient: OwnRecordUpdate, db: DbSession, settings: SettingsDep) -> Response:
    """Sign out on the server: every session this patient holds is refused from now on."""
    sign_out_everywhere(db, patient)
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    end_session(response, settings)
    return response


# --------------------------------------------------------------------------------------
# Consent (Issue 21): per purpose, defaulting to no, withdrawable at any time
# --------------------------------------------------------------------------------------


def _consent_state(
    db: DbSession, patient: Patient, notice: str | None = None
) -> ConsentStateOut:
    """Every question, its wording and the patient's current answer."""
    answers = consent_service.consent_state(db, patient.id)
    return ConsentStateOut(
        intro=CONSENT_INTRO,
        wording_version=CONSENT_WORDING_VERSION,
        notice=notice,
        answers=[
            ConsentAnswerOut(
                purpose=purpose, question=CONSENT_WORDING[purpose], granted=granted
            )
            for purpose, granted in answers.items()
        ],
    )


@router.get(
    "/me/consents", response_model=ConsentStateOut, operation_id="patientsConsentRead"
)
def get_my_consents(patient: OwnRecordRead, db: DbSession) -> ConsentStateOut:
    """What the patient has been asked, and what they have answered. No answer means no."""
    return _consent_state(db, patient)


@router.put(
    "/me/consents/{purpose}",
    response_model=ConsentStateOut,
    operation_id="patientsConsentUpdate",
)
def set_my_consent(
    purpose: ConsentPurpose,
    body: ConsentUpdateIn,
    patient: OwnRecordUpdate,
    db: DbSession,
) -> ConsentStateOut:
    """Give or withdraw one consent. It takes effect at once: the next board render and the next
    message both read the answer this writes."""
    consent_service.record_consent(
        db, patient, purpose, granted=body.granted, channel=PatientChannel.WEB
    )
    db.commit()
    return _consent_state(
        db, patient, notice=None if body.granted else CONSENT_WITHDRAWN_NOTICE
    )
