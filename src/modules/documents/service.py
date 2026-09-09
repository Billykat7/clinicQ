"""Document storage domain logic — ingest, links, integrity, retention (Issue #70).

Every read/write of the consolidated ``document`` table goes through this layer so the router stays
thin and one set of rules governs the context. The invariants owned here:

* **Nothing infected is ever stored** — :func:`ingest_document` scans the raw bytes *before* the row
  is committed and refuses an ``infected`` upload (:class:`DocumentInfectedError`), with nothing
  written to storage or the database.
* **Every stored object carries a checksum** — the SHA-256 of the bytes written at ingest is
  recorded on the row. :func:`read_document_bytes` re-hashes what it reads and raises
  :class:`DocumentChecksumMismatchError` on any mismatch, so corruption or tampering fails the
  download loudly rather than serving bad bytes.
* **Bytes leave only through a signed link** — written under an opaque key in private storage, never
  exposed in a response, and only ever handed out through a short-lived signed link; every download
  is audited with the actor, the document and a timestamp.
* **Retention is automatic and recorded** — the ingest stamps ``expires_at`` from the document's
  retention class, and :func:`run_retention_sweep` purges anything past it: the bytes are removed,
  the row is tombstoned (``deleted_at`` / ``deletion_reason``, soft-deleted) and an audit line is
  written, so a deletion is never silent.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    BoundedContext,
    SecurityAuditEvent,
    SecurityAuditOutcome,
)
from src.commons.exceptions import (
    DocumentChecksumMismatchError,
    DocumentInfectedError,
)
from src.commons.schemas import ModuleInfo
from src.core.config import get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import Document

# Re-exported so the router resolves the acting user through the documents service only,
# never reaching into another module directly.
from src.modules.account.users import find_user_id_by_email  # noqa: F401
from src.modules.documents.download_links import create_document_download_token
from src.modules.documents.enums import (
    DOCUMENT_FILE_EXTENSION,
    DocumentContentType,
    DocumentOwnerType,
    DocumentType,
    RetentionClass,
    VirusScanStatus,
)
from src.modules.documents.retention import expiry_for
from src.modules.documents.schemas import DocumentRead
from src.modules.documents.storage import LocalObjectStorage, compute_checksum
from src.modules.documents.virus_scan import DocumentScanner

logger = logging.getLogger(__name__)

# Stable list sort: newest first, id as the deterministic tie-breaker so pagination never drops or
# repeats a row across pages.
_LIST_ORDER = (Document.created_at.desc(), Document.id)

# Reason stamped on ``deletion_reason`` when the retention sweep purges an expired document.
_RETENTION_DELETION_REASON = "retention_expired"
# Reason stamped when a caller explicitly deletes a document.
_MANUAL_DELETION_REASON = "manual"


def get_module_info() -> ModuleInfo:
    """Return stub module metadata for the documents bounded context."""
    return ModuleInfo(
        context=BoundedContext.DOCUMENTS,
        summary="Documents: one store for tenant, application, lease and maintenance files.",
    )


# --------------------------------------------------------------------------------------
# Mappers — ORM rows to API read models.
# --------------------------------------------------------------------------------------


def document_to_read(row: Document) -> DocumentRead:
    """Map a ``Document`` ORM row to its metadata projection (never bytes/storage key)."""
    return DocumentRead(
        id=row.id,
        owner_type=DocumentOwnerType(row.owner_type),
        owner_id=row.owner_id,
        document_type=DocumentType(row.document_type),
        original_filename=row.original_filename,
        content_type=row.content_type,
        byte_size=row.byte_size,
        checksum_sha256=row.checksum_sha256,
        retention_class=RetentionClass(row.retention_class),
        expires_at=row.expires_at,
        virus_scan_status=VirusScanStatus(row.virus_scan_status),
        uploaded_by=row.uploaded_by,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        modified_at=row.modified_at,
    )


# --------------------------------------------------------------------------------------
# Reads.
# --------------------------------------------------------------------------------------


def get_active_document(db: Session, document_id: str) -> Document | None:
    """Load a single non-deleted document by id, or ``None`` when absent/soft-deleted."""
    return db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def list_documents(
    db: Session,
    *,
    owner_type: DocumentOwnerType,
    owner_id: str,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Document], int]:
    """Return a page of one owner's non-deleted documents plus the total.

    Documents are always listed for a single ``(owner_type, owner_id)`` pair — the composite the
    table is indexed on. Results use a stable ``(created_at DESC, id)`` sort so paging is
    deterministic.
    """
    filters = (
        Document.is_deleted.is_(False),
        Document.owner_type == owner_type.value,
        Document.owner_id == owner_id,
    )
    total = int(
        db.execute(
            select(func.count()).select_from(Document).where(*filters)
        ).scalar_one()
    )
    rows = (
        db.execute(
            select(Document)
            .where(*filters)
            .order_by(*_LIST_ORDER)
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --------------------------------------------------------------------------------------
# Ingest — scan, checksum, store.
# --------------------------------------------------------------------------------------


def _storage_key(
    *, owner_type: DocumentOwnerType, owner_id: str, document_id: str, extension: str
) -> str:
    """Build the opaque private-storage key a document's bytes are written under.

    Namespaced by owner type, owner id then document id, so keys are stable, collision-free and never
    derived from user-controlled filenames.
    """
    return f"documents/{owner_type.value}/{owner_id}/{document_id}.{extension}"


def ingest_document(
    db: Session,
    storage: LocalObjectStorage,
    scanner: DocumentScanner,
    *,
    owner_type: DocumentOwnerType,
    owner_id: str,
    document_type: DocumentType,
    retention_class: RetentionClass,
    data: bytes,
    original_filename: str,
    content_type: DocumentContentType,
    uploaded_by: str | None = None,
) -> Document:
    """Scan, checksum and store ``data``, then persist the document row.

    The bytes are **scanned first**: an ``infected`` verdict refuses the upload with nothing written.
    Otherwise the SHA-256 is recorded, the bytes are written under an opaque key *before* the row is
    committed, and ``expires_at`` is stamped from the retention class. If the insert fails the object
    is removed so no orphan is left behind. The content-type allow-list and size cap are enforced
    upstream in the router.

    Raises:
        DocumentInfectedError: the virus scan flagged the bytes; nothing is stored.
    """
    scan_status = scanner.scan(data)
    if scan_status is VirusScanStatus.INFECTED:
        raise DocumentInfectedError(
            filename=original_filename, scan_status=scan_status.value
        )

    document_id = str(uuid4())
    extension = DOCUMENT_FILE_EXTENSION[content_type]
    storage_key = _storage_key(
        owner_type=owner_type,
        owner_id=owner_id,
        document_id=document_id,
        extension=extension,
    )
    checksum = compute_checksum(data)
    storage.save(storage_key, data)

    now = datetime.now(APP_TIMEZONE)
    row = Document(
        id=document_id,
        owner_type=owner_type.value,
        owner_id=owner_id,
        document_type=document_type.value,
        storage_key=storage_key,
        original_filename=original_filename,
        content_type=content_type.value,
        byte_size=len(data),
        checksum_sha256=checksum,
        retention_class=retention_class.value,
        expires_at=expiry_for(retention_class, ingested_at=now),
        virus_scan_status=scan_status.value,
        uploaded_by=uploaded_by,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(storage_key)
        raise
    db.refresh(row)
    return row


# --------------------------------------------------------------------------------------
# Download — signed link, integrity-checked read, audit.
# --------------------------------------------------------------------------------------


def create_download_link(
    document: Document, *, actor: str | None
) -> tuple[str, datetime]:
    """Mint a short-lived signed download token for ``document`` and return it with its expiry.

    Returns ``(token, expires_at)``. The token is scoped to this one document and to the acting user
    (for the audit trail), and stops working at ``expires_at`` — a lifetime of
    ``document_link_expire_seconds``.
    """
    token = create_document_download_token(document.id, actor=actor)
    expires_at = datetime.now(APP_TIMEZONE) + timedelta(
        seconds=get_settings().document_link_expire_seconds
    )
    return token, expires_at


def read_document_bytes(storage: LocalObjectStorage, document: Document) -> bytes:
    """Return ``document``'s stored bytes, verifying them against the recorded checksum.

    Raises:
        FileNotFoundError: the backing object is missing (e.g. removed out of band).
        DocumentChecksumMismatchError: the bytes no longer match the recorded checksum — the read
            fails loudly rather than serving corrupted or tampered content.
    """
    data = storage.read(document.storage_key)
    expected = document.checksum_sha256
    if expected is not None:
        actual = compute_checksum(data)
        if actual != expected:
            raise DocumentChecksumMismatchError(
                document.id, expected=expected, actual=actual
            )
    return data


def log_download(document: Document, *, actor: str | None) -> None:
    """Write a ``SECURITY_AUDIT`` line recording a document download (Issue #70)."""
    _log_event(
        SecurityAuditEvent.DOCUMENT_DOWNLOAD,
        actor=actor,
        payload={
            "document_id": document.id,
            "owner_type": document.owner_type,
            "owner_id": document.owner_id,
        },
    )


# --------------------------------------------------------------------------------------
# Deletion — explicit and retention-driven.
# --------------------------------------------------------------------------------------


def soft_delete_document(
    db: Session,
    storage: LocalObjectStorage,
    document: Document,
    *,
    reason: str = _MANUAL_DELETION_REASON,
    actor: str | None = None,
) -> None:
    """Purge a document's bytes and tombstone the row (excluded from later reads; row survives).

    The bytes are removed from storage and the row is soft-deleted with ``deleted_at`` /
    ``deletion_reason`` stamped, so the deletion is recorded rather than silent. A retention-driven
    purge additionally audits the event via :func:`run_retention_sweep`.
    """
    storage.delete(document.storage_key)
    document.is_deleted = True
    document.is_active = False
    document.deleted_at = datetime.now(APP_TIMEZONE)
    document.deletion_reason = reason
    db.commit()


def run_retention_sweep(
    db: Session,
    storage: LocalObjectStorage,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Purge every live document whose retention has expired; return the purged document ids.

    Selects non-deleted documents with an ``expires_at`` at or before ``now`` (default: the current
    Johannesburg time), removes each one's bytes, tombstones the row and writes a
    ``DOCUMENT_RETENTION_DELETED`` audit line carrying the document, its owner and a timestamp — so
    each deletion is recorded, never silent. Documents with a ``permanent`` retention class have a
    null ``expires_at`` and are never selected. Idempotent: a re-run finds nothing already purged.
    """
    cutoff = now or datetime.now(APP_TIMEZONE)
    expired = (
        db.execute(
            select(Document).where(
                Document.is_deleted.is_(False),
                Document.expires_at.is_not(None),
                Document.expires_at <= cutoff,
            )
        )
        .scalars()
        .all()
    )
    purged: list[str] = []
    for document in expired:
        soft_delete_document(
            db,
            storage,
            document,
            reason=_RETENTION_DELETION_REASON,
        )
        _log_event(
            SecurityAuditEvent.DOCUMENT_RETENTION_DELETED,
            actor=None,
            payload={
                "document_id": document.id,
                "owner_type": document.owner_type,
                "owner_id": document.owner_id,
                "retention_class": document.retention_class,
            },
        )
        purged.append(document.id)
    return purged


# --------------------------------------------------------------------------------------
# Audit logging.
# --------------------------------------------------------------------------------------


def _log_event(
    event: SecurityAuditEvent, *, actor: str | None, payload: dict[str, Any]
) -> None:
    """Write a ``SECURITY_AUDIT`` line for a documents domain action (Issue #70).

    The line carries the event, a success outcome, the acting user, the supplied identifiers and a
    Johannesburg timestamp, as a sorted-key JSON payload so log aggregators can key on the
    ``document_*`` events.
    """
    body: dict[str, Any] = {
        "event": event.value,
        "outcome": SecurityAuditOutcome.SUCCESS.value,
        "actor": actor,
        "timestamp": datetime.now(APP_TIMEZONE).isoformat(),
        **payload,
    }
    logger.info(
        "SECURITY_AUDIT %s",
        json.dumps(body, sort_keys=True, default=str),
        extra={"security_audit_event": event.value},
    )
