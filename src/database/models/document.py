"""Document model: one consolidated record for every stored file (Issue #70).

By M11 four modules stored files four slightly different ways — tenant documents, application
documents, lease documents and maintenance attachments — each with its own table, storage and
signed-link plumbing. This is the single record they consolidate onto: one row per stored object,
with consistent access control, integrity, retention and auditing.

The row is deliberately small and describes the object, never the caller:

* ``owner_type`` + ``owner_id`` are the **polymorphic owner reference** — the kind of entity the
  document belongs to (:class:`~src.modules.documents.enums.DocumentOwnerType`) and its id. There is
  no database foreign key to the owner precisely because the owner may be any of several tables;
  RBAC on the document is inherited from the owner's domain instead (see ``OWNER_TYPE_RESOURCE``).
* ``storage_key`` is the opaque private-storage key the bytes live under — never a public URL. The
  bytes leave only through a short-lived signed link, and every download is audited.
* ``checksum_sha256`` is the SHA-256 of the bytes written at ingest. The download path re-hashes what
  it reads and compares, so silent corruption or tampering fails the download loudly rather than
  serving bad bytes. Nullable only for legacy rows backfilled from a per-module store that were
  never re-hashed through this service (they carry ``virus_scan_status = pending``).
* ``retention_class`` drives automatic expiry: the ingest stamps ``expires_at`` from the class's
  lifetime (``None`` = kept indefinitely), and the daily retention sweep purges anything past it,
  stamping ``deleted_at`` / ``deletion_reason`` and writing an audit line so a deletion is never
  silent.
* ``virus_scan_status`` records the ingest scan verdict; only a ``clean`` (or, when scanning is off,
  ``skipped``) document is ever committed.
* ``uploaded_by`` records the acting ``User`` for the audit trail; ``SET NULL`` so the record
  survives the uploading user being removed.

Includes TimestampMixin (created_at/modified_at), ActiveMixin (is_active), and SoftDeleteMixin
(is_deleted). A retention purge soft-deletes the row (keeping the tombstone for audit) and removes
the underlying bytes.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin


class Document(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """One stored file: polymorphic owner, type, storage key, checksum, retention and scan status."""

    __tablename__ = "document"
    __table_args__ = (
        # Documents are always listed for one owner, so the polymorphic owner reference is the
        # primary access path and is indexed as a composite.
        Index(
            "ix_clinicq_document_owner",
            "owner_type",
            "owner_id",
        ),
        # The retention sweep scans for rows whose expiry has passed, so ``expires_at`` is indexed.
        Index(
            "ix_clinicq_document_expires_at",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    owner_type: Mapped[str] = mapped_column(String(30), nullable=False)
    """Kind of entity the document belongs to; one of
    :class:`~src.modules.documents.enums.DocumentOwnerType`. With ``owner_id`` this is the
    polymorphic owner reference — no DB FK, because the owner may be any of several tables."""
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    """Id of the owning entity (a tenant, application, lease or maintenance request)."""
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    """What the document is; one of :class:`~src.modules.documents.enums.DocumentType`."""
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    """Opaque private-storage key the bytes live under (never a public URL)."""
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    """The client's filename, kept for display and the download's ``Content-Disposition``."""
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    """IANA media type of the stored bytes; one of
    :class:`~src.modules.documents.enums.DocumentContentType`. Bytes are stored verbatim, so this is
    exactly the accepted type the client declared."""
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    """Size in bytes of the stored object (enforced against the upload cap on ingest)."""
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Lowercase-hex SHA-256 of the bytes written at ingest, verified again on download. Null only
    for legacy rows backfilled from a per-module store and never re-hashed through this service."""
    retention_class: Mapped[str] = mapped_column(String(30), nullable=False)
    """How long the document is kept; one of
    :class:`~src.modules.documents.enums.RetentionClass`. Drives ``expires_at``."""
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the retention sweep may purge the document; null means kept indefinitely (a
    ``permanent`` retention class). Indexed for the sweep."""
    virus_scan_status: Mapped[str] = mapped_column(String(20), nullable=False)
    """Verdict of the ingest virus scan; one of
    :class:`~src.modules.documents.enums.VirusScanStatus`. Only ``clean``/``skipped`` rows exist on
    a live document; ``pending`` marks a legacy backfilled row."""
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """User id that uploaded the document (audit trail). ``SET NULL`` so the record survives the
    uploading user being removed; null when the uploader is not resolvable."""
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the document's bytes were purged (by the retention sweep or an explicit delete); null
    while the document is live. Paired with ``deletion_reason`` so a deletion is recorded, not
    silent."""
    deletion_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """Why the document was deleted (e.g. ``retention_expired``, ``manual``); null while live."""
