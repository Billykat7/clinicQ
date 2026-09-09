"""Documents-module enumerations (Issue #70).

By M11 four modules stored files four slightly different ways. This bounded context consolidates
them behind one ``document`` record, so the vocabularies that govern *whose* a document is, *what*
it is, *how long it is kept* and *whether it passed its virus scan* are defined once here and never
appear as magic strings in the model, service, router or migration.

* :class:`DocumentOwnerType` — the kind of entity a document belongs to; stored in
  ``document.owner_type`` alongside the opaque ``owner_id``. This is the polymorphic owner reference
  that lets one table hold tenant, application, lease and maintenance attachments.
* :class:`DocumentType` — *what* the document is (an ID copy, a signed lease, a maintenance photo…);
  stored in ``document.document_type``. Cross-cutting, so a single vocabulary spans every owner type.
* :class:`RetentionClass` — *how long* a document is kept; stored in ``document.retention_class``.
  Its :data:`RETENTION_PERIOD_DAYS` maps each class to a lifetime in days (``None`` = kept
  indefinitely), from which the ingest stamps ``expires_at`` and the retention sweep decides what to
  purge.
* :class:`DocumentContentType` — the upload content-type allow-list (documents *and* media). Anything
  outside this set is a 415, checked before the body is buffered.
* :class:`VirusScanStatus` — the verdict of the ingest virus scan; stored in
  ``document.virus_scan_status``. Only a ``CLEAN`` (or, when scanning is off, ``SKIPPED``) document is
  ever committed — an ``INFECTED`` upload is refused at the door.
"""

from enum import StrEnum


class DocumentOwnerType(StrEnum):
    """The kind of entity a document belongs to. Stored in ``document.owner_type``.

    The polymorphic half of the owner reference (``owner_type`` + ``owner_id``) that lets one table
    hold what would otherwise be a per-module attachment store each. A document's access control is
    **inherited from its owner** (see :data:`OWNER_TYPE_RESOURCE`): reaching a document is exactly
    reaching the thing it is attached to, so a new owner type needs no new grant, no new resource
    and no place in anyone's role matrix.

    - ``USER``: a file on a person's own profile.

    Add a member per kind of record files hang off in your domain, then two things and no more: an
    entry in :data:`OWNER_TYPE_RESOURCE` naming the resource that governs it, and a branch in
    ``src.modules.documents.router._owner_identity`` resolving the instance the scope resolver
    narrows by.
    """

    USER = "user"


OWNER_TYPE_RESOURCE: dict[DocumentOwnerType, str] = {
    DocumentOwnerType.USER: "users",
}


class DocumentType(StrEnum):
    """What a stored document is. Stored in ``document.document_type``.

    A single cross-cutting vocabulary spanning every owner type, so the consolidated table describes
    its contents the same way whether the file came from tenants, applications, leases or
    maintenance. ``OTHER`` is the catch-all for anything without a dedicated kind.
    """

    ID_DOCUMENT = "id_document"
    PROOF_OF_INCOME = "proof_of_income"
    SIGNED_LEASE = "signed_lease"
    LEASE_ADDENDUM = "lease_addendum"
    # E-signature completion certificate (Issue #71) — the provider's audit record of a signing
    # (signer identity, timestamps, IP/consent), retained alongside the executed document so the
    # signature is provable long after the fact.
    COMPLETION_CERTIFICATE = "completion_certificate"
    RECEIPT = "receipt"
    MAINTENANCE_PHOTO = "maintenance_photo"
    MAINTENANCE_VIDEO = "maintenance_video"
    OTHER = "other"


class RetentionClass(StrEnum):
    """How long a document is kept. Stored in ``document.retention_class``.

    Retention is a property of the *document*, not of the caller: the ingest stamps ``expires_at``
    from :data:`RETENTION_PERIOD_DAYS`, and the daily retention sweep purges anything past it. A
    class is chosen for *why* the document exists, so the right rows expire automatically.

    - ``PERMANENT``: kept indefinitely — a signed lease, an ID copy, anything with a legal or
      contractual reason to survive. Never expires (:data:`RETENTION_PERIOD_DAYS` is ``None``).
    - ``STANDARD``: the default operational retention — kept for years, then purged. Used for
      ordinary supporting documents that need not live forever.
    - ``TRANSIENT``: short-lived — e.g. the supporting documents of a *rejected* applicant, which
      have no reason to be retained once the decision is made. Purged after a short window.
    """

    PERMANENT = "permanent"
    STANDARD = "standard"
    TRANSIENT = "transient"


# Retention class -> lifetime in days from ingest (``None`` = kept indefinitely). ``STANDARD`` is
# seven years, a common commercial record-keeping horizon; ``TRANSIENT`` is thirty days, enough for a
# rejected applicant's documents to be recoverable briefly before they are purged. Defined once here
# so the ingest (which stamps ``expires_at``) and the sweep (which reads it) never disagree.
RETENTION_PERIOD_DAYS: dict[RetentionClass, int | None] = {
    RetentionClass.PERMANENT: None,
    RetentionClass.STANDARD: 365 * 7,
    RetentionClass.TRANSIENT: 30,
}


class DocumentContentType(StrEnum):
    """IANA media types accepted for a document upload (Issue #70).

    The union of what the consolidated modules stored: PDFs and images (tenant documents, inspection
    and maintenance photos) plus the two video types maintenance attachments allowed. Values are the
    exact ``Content-Type`` strings a client sends, so the router compares against these rather than
    raw literals; anything outside the set is a 415.
    """

    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"
    MP4 = "video/mp4"
    WEBM = "video/webm"


# File extension stored in a document's storage key per content type; used only to build a
# human-recognisable key, never for validation (the content-type allow-list is the gate).
DOCUMENT_FILE_EXTENSION: dict[DocumentContentType, str] = {
    DocumentContentType.PDF: "pdf",
    DocumentContentType.JPEG: "jpg",
    DocumentContentType.PNG: "png",
    DocumentContentType.WEBP: "webp",
    DocumentContentType.MP4: "mp4",
    DocumentContentType.WEBM: "webm",
}


class VirusScanStatus(StrEnum):
    """Verdict of the ingest virus scan. Stored in ``document.virus_scan_status``.

    Scanning runs on the raw bytes before the row is committed, so only a document that is safe to
    keep is ever stored:

    - ``CLEAN``: the scanner passed the bytes; the document is stored and downloadable.
    - ``INFECTED``: the scanner flagged the bytes; the upload is refused (422) with nothing written,
      so this value is never actually persisted on a live row — it exists to name the rejection.
    - ``SKIPPED``: scanning was disabled for the deployment (``DOCUMENT_VIRUS_SCAN_ENABLED=false``),
      so the bytes were stored unscanned; recorded honestly rather than claimed clean.
    - ``PENDING``: a document whose scan has not been recorded — the state a legacy row backfilled
      from a per-module store carries until it is re-ingested through this service.
    """

    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
    SKIPPED = "skipped"
