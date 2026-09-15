"""A patient's web session: issued after a phone OTP, checked on every patient route (Issue 17).

A patient has no refresh token: the session is one typed JWT (``patient``) in an httpOnly,
``SameSite=Lax`` cookie (``Secure`` outside development), good for ``PATIENT_SESSION_HOURS``, plus
the readable CSRF cookie bound to the session. Signing out increments ``session_version`` on the
patient row, which every token issued so far carries, so a sign-out is enforced on the server, not
only by clearing a cookie.

:func:`get_current_patient` is the dependency every patient route takes. It accepts the cookie or an
``Authorization: Bearer`` token (the app channel, M9), and only a ``patient`` token: a staff access
token opens no patient route, and a patient token opens no staff one.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.ids import new_id
from src.core.config import Settings, get_settings
from src.core.csrf_middleware import mint_csrf_token
from src.core.security import (
    create_patient_session_token,
    decode_patient_session_token,
    security_scheme,
)
from src.database.models import Patient
from src.database.session import get_db


def _unauthenticated() -> HTTPException:
    """The one 401 a patient route answers: no session, a bad one, or one signed out."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in with your phone number first.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def start_session(response: Response, patient: Patient, settings: Settings) -> str:
    """Set the patient's session and CSRF cookies on ``response``; return the session id."""
    sid = new_id()
    max_age = settings.patient_session_hours * 3600
    secure = not settings.is_development
    response.set_cookie(
        key=settings.patient_session_cookie_name,
        value=create_patient_session_token(patient.id, sid, patient.session_version),
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=mint_csrf_token(sid),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return sid


def end_session(response: Response, settings: Settings) -> None:
    """Clear the patient's session and CSRF cookies."""
    response.delete_cookie(
        settings.patient_session_cookie_name, path="/", samesite="lax"
    )
    response.delete_cookie(settings.csrf_cookie_name, path="/", samesite="lax")


def _claims(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> dict[str, Any] | None:
    """The patient-session claims from the Bearer header or the cookie, or None."""
    token = (
        credentials.credentials
        if credentials is not None and credentials.credentials
        else request.cookies.get(get_settings().patient_session_cookie_name)
    )
    return decode_patient_session_token(token) if token else None


def get_current_patient(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(security_scheme)
    ] = None,
) -> Patient:
    """Dependency: the signed-in patient, or 401.

    Refuses a missing or non-``patient`` token, a patient that no longer exists or was deleted, and
    a token issued before the patient's last sign-out (an older ``session_version``).
    """
    claims = _claims(request, credentials)
    if claims is None:
        raise _unauthenticated()
    patient = db.execute(
        select(Patient).where(Patient.id == str(claims.get("sub") or ""))
    ).scalar_one_or_none()
    if patient is None or patient.is_deleted:
        raise _unauthenticated()
    if claims.get("ver") != patient.session_version:
        raise _unauthenticated()
    return patient


def signed_in_patient_id(request: Request, db: Session) -> str | None:
    """The id of the patient whose own session made this request, or ``None``. Never raises.

    For a page anyone with a link may open (the ticket page, Issue 68) that offers more to the ticket's
    own patient: the same checks as :func:`get_current_patient`, answering "nobody" instead of 401.
    """
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    bearer = (
        HTTPAuthorizationCredentials(scheme=scheme, credentials=token.strip())
        if scheme.lower() == "bearer" and token.strip()
        else None
    )
    claims = _claims(request, bearer)
    if claims is None:
        return None
    patient = db.execute(
        select(Patient).where(Patient.id == str(claims.get("sub") or ""))
    ).scalar_one_or_none()
    if patient is None or patient.is_deleted:
        return None
    if claims.get("ver") != patient.session_version:
        return None
    return patient.id


#: The signed-in patient, for a route signature: ``patient: CurrentPatient``.
CurrentPatient = Annotated[Patient, Depends(get_current_patient)]


def sign_out_everywhere(db: Session, patient: Patient) -> None:
    """End every session the patient has: every token issued so far is refused. The caller commits."""
    patient.session_version += 1
