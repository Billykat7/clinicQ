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
from src.modules.patients import proxy, service
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
    DependantCodeIn,
    DependantIn,
    DependantListOut,
    DependantOut,
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
        # A dependant with no phone of their own (Issue 84) has no number to mask.
        phone=mask_phone(patient.phone_e164) if patient.phone_e164 else "",
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
    inside the resend cooldown or over the request budget, 503 (``patients.otp.sms_disabled``) while
    SMS is switched off for the deployment (``SMS_ENABLED``).
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


# --------------------------------------------------------------------------------------
# One phone, a household: the people this patient may act for (Issue 84)
# --------------------------------------------------------------------------------------


def _dependants(db: Session, patient: Patient) -> DependantListOut:
    rows = proxy.dependants(db, patient.id)
    return DependantListOut(
        total=len(rows),
        items=[
            DependantOut(
                link_id=row.link_id,
                patient_id=row.patient_id,
                name=row.name,
                relationship=row.relationship,
                has_phone=row.has_phone,
            )
            for row in rows
        ],
        max_dependants=proxy.MAX_DEPENDANTS,
    )


@router.get(
    "/me/dependants",
    response_model=DependantListOut,
    operation_id="patientsDependants",
    summary="The people I may book and join queues for",
)
def my_dependants(patient: OwnRecordRead, db: DbSession) -> DependantListOut:
    """Everyone this patient acts for, oldest link first. A link that was ended is not here."""
    return _dependants(db, patient)


@router.post(
    "/me/dependants/code",
    response_model=OtpRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="patientsDependantCode",
    summary="Send a code to the number of somebody I want to act for",
)
def request_dependant_code(
    body: DependantCodeIn, request: Request, patient: OwnRecordUpdate, db: DbSession
) -> OtpRequestOut:
    """The verification step: only somebody holding that phone can finish the link.

    422 for a number that cannot be read, 429 inside the cooldown or over the budget, 409 for the
    caller's own number, a person they already act for, or more links than they may hold, and 503
    while SMS is switched off.
    """
    try:
        sent = proxy.send_link_code(
            db, patient, raw_phone=body.phone, ip=client_ip_or_unknown(request)
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


@router.post(
    "/me/dependants",
    response_model=DependantListOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="patientsDependantAdd",
    summary="Act for somebody: with the code sent to their phone, or for somebody who has none",
)
def add_dependant(
    body: DependantIn, patient: OwnRecordUpdate, db: DbSession
) -> DependantListOut:
    """Make the link, record their consent to it, and audit both people.

    With ``phone`` and ``code``: the code proves the number, and the person keeps their own record.
    Without: a new record for somebody with no phone of their own, such as a small child, who can
    never sign in and whose messages go to this patient's phone. 400 for a wrong or expired code.
    """
    if body.phone and body.code:
        try:
            proxy.link_with_code(
                db,
                patient,
                raw_phone=body.phone,
                code=body.code,
                relationship=body.relationship,
                name=body.name,
            )
        except service.OtpRejectedError as exc:
            return JSONResponse(  # type: ignore[return-value]
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "detail": _REJECTED[exc.outcome],
                    "code": f"patients.otp.{exc.outcome.value}",
                    "attempts_left": exc.attempts_left,
                },
            )
    elif body.name and not body.phone:
        proxy.link_without_phone(
            db, patient, name=body.name, relationship=body.relationship
        )
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Give the code sent to their number, or a name for somebody with no phone.",
        )
    db.commit()
    return _dependants(db, patient)


@router.delete(
    "/me/dependants/{link_id}",
    response_model=DependantListOut,
    operation_id="patientsDependantRevoke",
    summary="Stop acting for somebody, or stop somebody acting for me",
)
def revoke_dependant(
    link_id: str, patient: OwnRecordUpdate, db: DbSession
) -> DependantListOut:
    """End a link, from either side. The next action through it is refused; tickets already taken stay."""
    proxy.revoke(db, patient, link_id)
    db.commit()
    return _dependants(db, patient)
