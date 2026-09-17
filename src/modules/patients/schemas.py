"""Request and response models for the patient identity API (Issues 17, 219).

A contact arrives as the patient typed it and is normalised by the service, so the schemas only
bound its length. A code is exactly the configured number of digits.

A sign-in names **exactly one** contact — a ``phone`` or an ``email``, never both and never
neither. That is a schema rule rather than a service one so the answer comes back as a 422 before
anything is issued, counted or looked up, and so the OpenAPI schema states it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.commons.email_address import MAX_EMAIL_LENGTH
from src.commons.enums import ConsentPurpose, ProxyRelationship

#: What a request naming both contacts, or neither, is told. One sign-in proves one contact: which
#: one it is decides where the code goes and which patient it lands on, so the API will not guess.
ONE_CONTACT_ONLY = "Give a mobile number or an email address, not both."


class _OneContact(BaseModel):
    """Exactly one of ``phone`` and ``email``, for the two sign-in requests that take a contact."""

    @model_validator(mode="after")
    def _exactly_one_contact(self) -> Self:
        """Refuse a request naming both contacts or neither, before anything is issued."""
        phone = getattr(self, "phone", None)
        email = getattr(self, "email", None)
        if bool(phone) == bool(email):
            raise ValueError(ONE_CONTACT_ONLY)
        return self


class OtpRequestIn(_OneContact):
    """Ask for a sign-in code, for a phone number or an email address."""

    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(
        default=None,
        min_length=6,
        max_length=32,
        description="The mobile number, as typed. The code goes by SMS.",
    )
    email: str | None = Field(
        default=None,
        min_length=3,
        max_length=MAX_EMAIL_LENGTH,
        description=(
            "The email address, as typed. The code goes by email. Accepted only where "
            "PATIENT_EMAIL_SIGN_IN_ENABLED is on; otherwise 503 patients.otp.email_disabled."
        ),
    )


#: What a patient is told about their number when they give it (POPIA s18: why it is collected).
#: Wording owned by F (Data & Research); draft v1, awaiting F's review before the patient pages
#: (M9) show it. Plain words, no legal terms, readable on a feature phone's screen.
PHONE_NOTICE = (
    "We use your number to check it is you and to find your place in the queue. We never show it "
    "on a screen or give it to anyone else. We only send you messages you ask for."
)

#: The same promise about an address (Issue 219), in the same plain words and for the same reason.
#: Also F's to confirm, and deliberately the phone notice with one noun changed: a patient reading
#: both should not have to work out whether ClinicQ treats the two differently. It does not.
EMAIL_NOTICE = (
    "We use your email address to check it is you and to find your place in the queue. We never "
    "show it on a screen or give it to anyone else. We only send you messages you ask for."
)

#: What the answer says a code is on its way by. The route picks one; neither says anything about
#: whether the contact already belongs to a patient, because that would be an enumeration oracle.
SMS_CODE_SENT = "A 6-digit code is on its way by SMS."
EMAIL_CODE_SENT = "A 6-digit code is on its way by email."


class OtpRequestOut(BaseModel):
    """The same answer for every contact that could be sent a code (no enumeration)."""

    detail: str = SMS_CODE_SENT
    notice: str = Field(
        default=PHONE_NOTICE, description="Why the contact is collected."
    )
    expires_in_seconds: int = Field(description="How long the code is good for.")
    resend_after_seconds: int = Field(description="When another code may be requested.")


class OtpVerifyIn(_OneContact):
    """Prove the contact with the code it received. The same one the code was asked for."""

    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(default=None, min_length=6, max_length=32)
    email: str | None = Field(default=None, min_length=3, max_length=MAX_EMAIL_LENGTH)
    code: str = Field(
        pattern=r"^\d{4,8}$", description="The code from the SMS or the email."
    )


class PatientOut(BaseModel):
    """The signed-in patient, as their own session sees them. Both contacts are masked."""

    id: str
    phone: str = Field(
        description="The number, masked: +27 ** *** 4567. Empty if none."
    )
    email: str = Field(
        default="",
        description="The address, masked: n••••a@gmail.com (Issue 219). Empty if none.",
    )
    display_name: str | None
    phone_verified_at: datetime | None
    email_verified_at: datetime | None = None
    created_at: datetime


class PatientUpdateIn(BaseModel):
    """What a patient may change about themselves. Anything else is refused (422)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=80)


class ConsentAnswerOut(BaseModel):
    """One consent question, the words the patient was shown, and their current answer."""

    purpose: ConsentPurpose
    question: str = Field(description="The plain-language question, as shown.")
    granted: bool = Field(
        description="False until the patient says yes, on every channel."
    )


class ConsentStateOut(BaseModel):
    """Everything a patient is asked, and what they have answered so far."""

    intro: str
    wording_version: str
    answers: list[ConsentAnswerOut]
    notice: str | None = Field(
        default=None, description="What just happened, after an answer was changed."
    )


class ConsentUpdateIn(BaseModel):
    """A patient's answer to one question. Anything else is refused (422)."""

    model_config = ConfigDict(extra="forbid")

    granted: bool


class DependantCodeIn(BaseModel):
    """Ask for a code on the number of somebody this patient wants to act for (Issue 84)."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=6, max_length=32)


class DependantIn(BaseModel):
    """Finish a link: with the code sent to their number, or for somebody who has no phone."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=80)
    """What to call them on the patient's own screens. Required for somebody with no phone."""
    relationship: ProxyRelationship
    phone: str | None = Field(default=None, max_length=32)
    """Their number. With it, ``code`` must be the code sent to that number."""
    code: str | None = Field(default=None, min_length=4, max_length=8)


class DependantOut(BaseModel):
    """One person this patient may act for."""

    link_id: str
    patient_id: str
    name: str
    relationship: ProxyRelationship
    has_phone: bool = Field(
        description="True when they have a number of their own, proved with a code."
    )


class DependantListOut(BaseModel):
    """Everyone this patient may act for, oldest link first."""

    total: int
    items: list[DependantOut]
    max_dependants: int = Field(description="How many people one phone may act for.")
