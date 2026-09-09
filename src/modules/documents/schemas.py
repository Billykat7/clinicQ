"""Request/response models for the document storage API (Issue #70).

The document service is a manager-side platform surface: an authorised user ingests a file against a
polymorphic owner (a tenant, application, lease or maintenance request), lists a given owner's
documents, mints a short-lived signed link, and downloads the bytes only through that link. There is
no tenant self-service surface here — the per-module portals keep their own.

Read models never carry the ``storage_key``: it is an internal detail, and a document's bytes leave
solely through the download endpoint, and only via a short-lived signed link. The recorded
``checksum_sha256`` *is* surfaced — it is integrity metadata a caller can verify against, not a
secret.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.commons.enums import EsignEnvelopeStatus, EsignProviderKind
from src.modules.documents.enums import (
    DocumentOwnerType,
    DocumentType,
    RetentionClass,
    VirusScanStatus,
)


class DocumentRead(BaseModel):
    """Metadata for one stored document. Never carries the bytes or the ``storage_key``."""

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    owner_type: DocumentOwnerType
    owner_id: str
    document_type: DocumentType
    original_filename: str
    content_type: str
    byte_size: int
    checksum_sha256: str | None = Field(
        default=None,
        description="Lowercase-hex SHA-256 of the stored bytes; null only for legacy backfilled rows.",
    )
    retention_class: RetentionClass
    expires_at: datetime | None = Field(
        default=None,
        description="When the retention sweep may purge the document; null means kept indefinitely.",
    )
    virus_scan_status: VirusScanStatus
    uploaded_by: str | None = None
    is_active: bool
    created_at: datetime
    modified_at: datetime


class DocumentListOut(BaseModel):
    """Paginated listing of one owner's documents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[DocumentRead]
    total: int = Field(ge=0, description="Total documents for the owner (pre-paging).")


class DocumentLinkOut(BaseModel):
    """A short-lived signed download link for one document.

    ``download_url`` is the only way to fetch the bytes; it carries a signed token and stops working
    at ``expires_at``. The URL is host-agnostic (built from the request).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    download_url: str = Field(
        description="Signed, time-limited URL to fetch the bytes."
    )
    expires_at: datetime = Field(description="When the link stops working.")


# --------------------------------------------------------------------------------------
# E-signature integration (Issue #71).
# --------------------------------------------------------------------------------------


class EsignSendIn(BaseModel):
    """Request body to send a generated lease/addendum for signature.

    ``document_id`` is the generated document (a ``lease`` document produced in M6) to be signed;
    the envelope inherits its owner from that document, so the lease-activation gate can find it.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        min_length=1,
        max_length=36,
        description="Generated document to send for signature.",
    )
    recipient_email: EmailStr = Field(description="Signer's email address.")
    recipient_name: str = Field(
        min_length=1, max_length=200, description="Signer's display name."
    )
    subject: str | None = Field(
        default=None,
        max_length=255,
        description="Optional subject/title for the signing request.",
    )


class EsignPaperSignatureIn(BaseModel):
    """Request body to record a signature made on paper (provider-unavailable fallback)."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        min_length=1,
        max_length=36,
        description="Generated document that was signed on paper.",
    )
    recipient_email: EmailStr = Field(description="Signer's email address.")
    recipient_name: str = Field(
        min_length=1, max_length=200, description="Signer's display name."
    )


class EsignWebhookIn(BaseModel):
    """A provider webhook callback. Authenticity is the HMAC signature over the raw body.

    ``event`` is left a free string (not the terminal-event enum) so an intermediate provider event
    is accepted and ignored rather than rejected as invalid.
    """

    model_config = ConfigDict(extra="ignore")

    provider_envelope_id: str = Field(
        min_length=1, max_length=255, description="Provider's envelope id to correlate."
    )
    event: str = Field(min_length=1, max_length=40, description="Provider event type.")
    event_id: str = Field(
        min_length=1,
        max_length=255,
        description="Provider's unique event id (the idempotency key).",
    )
    reason: str | None = Field(
        default=None, max_length=2000, description="Decline reason, when declined."
    )


class EsignEnvelopeRead(BaseModel):
    """One signature envelope as returned by the API. Never carries a provider credential."""

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: str
    provider: EsignProviderKind
    owner_type: DocumentOwnerType
    owner_id: str
    source_document_id: str
    signed_document_id: str | None = None
    certificate_document_id: str | None = None
    status: EsignEnvelopeStatus
    recipient_email: str
    recipient_name: str
    subject: str | None = None
    is_paper: bool
    sent_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    decline_reason: str | None = None
    created_at: datetime
    modified_at: datetime


class EsignEnvelopeListOut(BaseModel):
    """Paginated listing of one owner's signature envelopes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[EsignEnvelopeRead]
    total: int = Field(ge=0, description="Total envelopes for the owner (pre-paging).")
