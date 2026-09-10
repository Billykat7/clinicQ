"""E-signature envelope model: one signing of a lease or addendum (Issue #71).

A lease is generated in M6 and, before this, printed, signed, scanned and emailed back. An
*envelope* is the record of sending that generated document to an e-signature provider and tracking
it to a terminal outcome, so the whole loop stays inside the product.

The row describes *one signing*, and links three documents on the unified store (Issue #70):

* ``source_document_id`` — the generated lease/addendum PDF that was sent for signature (never
  null; you always send a specific document). ``RESTRICT`` keeps it around while an envelope
  references it.
* ``signed_document_id`` — the executed document returned by the provider, stored on completion;
  null until the envelope is ``signed``. ``SET NULL`` so the envelope survives the document being
  removed.
* ``certificate_document_id`` — the provider's certificate of completion, retained alongside the
  signed document so the signature is provable long after the fact; null until completion.

``owner_type`` + ``owner_id`` is the polymorphic reference to what is being signed (a ``lease`` or,
for an addendum, the addendum's lease) — the same convention the document store uses — so the
lease-activation gate can ask "does this lease have a completed signature?" without a dedicated FK.

``provider_envelope_id`` is the provider's own id for the envelope, indexed because the webhook
correlates a callback back to this row by it. ``status`` is an
:class:`~src.commons.enums.EsignEnvelopeStatus` written only through
:mod:`src.modules.documents.esign_service`. ``is_paper`` marks the manual fallback recorded when the
provider is unavailable, so a lease can still be signed on paper without leaving the product.

Includes TimestampMixin (created_at/modified_at), ActiveMixin (is_active) and SoftDeleteMixin
(is_deleted).
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import EsignEnvelopeStatus, EsignProviderKind
from src.database.models.base import Base
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin


class EsignEnvelope(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """One signing of a lease/addendum: provider, recipient, status and the linked documents."""

    __tablename__ = "esign_envelope"
    __table_args__ = (
        # The activation gate and the per-lease listing both look an envelope up by what it signs,
        # so the polymorphic owner reference is indexed as a composite.
        Index(
            "ix_clinicq_esign_envelope_owner",
            "owner_type",
            "owner_id",
        ),
        # The webhook correlates a provider callback back to its row by the provider's envelope id.
        Index(
            "ix_clinicq_esign_envelope_provider_envelope_id",
            "provider_envelope_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    provider: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=EsignProviderKind.LOCAL.value,
    )
    """Which provider backs this envelope; one of
    :class:`~src.commons.enums.EsignProviderKind`. Recorded on the row so a portfolio can migrate
    providers without losing which one executed a given signing."""
    provider_envelope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The provider's own id for the envelope, set when it is sent; the key the webhook correlates
    a callback back to this row by. Null while the envelope is only ``created``."""
    owner_type: Mapped[str] = mapped_column(String(30), nullable=False)
    """Kind of entity being signed; a :class:`~src.modules.documents.enums.DocumentOwnerType`
    (``lease`` for a lease or its addendum). With ``owner_id`` this is the polymorphic reference the
    activation gate reads."""
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    """Id of the entity being signed (the lease id)."""
    source_document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("document.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """The generated lease/addendum document that was sent for signature. ``RESTRICT`` keeps it
    around while an envelope references it."""
    signed_document_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("document.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The executed (signed) document, stored on completion; null until the envelope is ``signed``.
    ``SET NULL`` so the envelope record survives the document being removed."""
    certificate_document_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("document.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The provider's certificate of completion, retained alongside the signed document; null until
    completion. ``SET NULL`` so the envelope record survives the document being removed."""
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=EsignEnvelopeStatus.CREATED.value,
        server_default=EsignEnvelopeStatus.CREATED.value,
    )
    """Lifecycle state; one of :class:`~src.commons.enums.EsignEnvelopeStatus` (``created`` on
    insert). Written only through the service; the terminal outcomes are applied idempotently from a
    verified webhook."""
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    """Email address the signing request is sent to (the signer)."""
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    """Display name of the signer, embedded in the signing request."""
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Optional human subject/title for the signing request; null falls back to a default."""
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the envelope was handed to the provider; null while only ``created``."""
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the envelope reached a terminal outcome (signed/declined/expired/voided); null before."""
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the signing window lapses; a provider that reports expiry drives the envelope to
    ``expired`` after this. Null when no window is set."""
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Why the recipient declined (from the provider's callback); null unless ``declined``."""
    is_paper: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    """Whether the signature was recorded via the manual paper fallback (provider unavailable)
    rather than executed electronically. The graceful-degradation path so a lease can still be
    signed on paper without leaving the product."""
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """User id that sent the envelope (audit trail). ``SET NULL`` so the record survives the user
    being removed; null when the actor is not resolvable."""
