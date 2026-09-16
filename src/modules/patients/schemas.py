"""Request and response models for the patient identity API (Issue 17).

A phone number arrives as the patient typed it and is normalised by the service, so the schemas
only bound its length. A code is exactly the configured number of digits.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.commons.enums import ConsentPurpose, ProxyRelationship


class OtpRequestIn(BaseModel):
    """Ask for a sign-in code for a phone number."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(
        min_length=6, max_length=32, description="The mobile number, as typed."
    )


#: What a patient is told about their number when they give it (POPIA s18: why it is collected).
#: Wording owned by F (Data & Research); draft v1, awaiting F's review before the patient pages
#: (M9) show it. Plain words, no legal terms, readable on a feature phone's screen.
PHONE_NOTICE = (
    "We use your number to check it is you and to find your place in the queue. We never show it "
    "on a screen or give it to anyone else. We only send you messages you ask for."
)


class OtpRequestOut(BaseModel):
    """The same answer for every number that could be sent a code (no enumeration)."""

    detail: str = "A 6-digit code is on its way by SMS."
    notice: str = Field(
        default=PHONE_NOTICE, description="Why the number is collected."
    )
    expires_in_seconds: int = Field(description="How long the code is good for.")
    resend_after_seconds: int = Field(description="When another code may be requested.")


class OtpVerifyIn(BaseModel):
    """Prove the number with the code it received."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=6, max_length=32)
    code: str = Field(pattern=r"^\d{4,8}$", description="The code from the SMS.")


class PatientOut(BaseModel):
    """The signed-in patient, as their own session sees them. The number is masked."""

    id: str
    phone: str = Field(description="The number, masked: +27 ** *** 4567.")
    display_name: str | None
    phone_verified_at: datetime | None
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
