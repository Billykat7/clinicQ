"""E-signature domain logic — send, track, execute and retain a signed lease (Issue #71).

Every read/write of the ``esign_envelope`` and ``esign_event`` tables goes through this layer so the
router stays thin and one set of rules governs the most legally sensitive flow in the product. The
invariants owned here:

* **Send goes through the provider interface** — :func:`send_for_signature` reads the generated
  lease/addendum from the unified document store, hands it to the pluggable provider and records a
  ``sent`` envelope correlated by the provider's envelope id. A provider outage
  (:class:`~src.modules.documents.esign.EsignSendError`) persists nothing and surfaces to the caller,
  who can fall back to paper — a provider being down never blocks a lease.
* **Webhooks are verified and idempotent** — signature verification happens at the router (over the
  raw body); :func:`handle_webhook` then records the provider's event id *first*
  (:class:`~src.database.models.esign_event.EsignEvent`, unique), so a re-delivered event trips the
  constraint and is skipped rather than re-applied. Only terminal events act; a replay onto an
  already-terminal envelope is a no-op.
* **The signed document and its certificate are both retained** — on completion the executed
  document and the provider's certificate of completion are ingested into the document store as
  ``permanent`` records owned by the lease, and linked back onto the envelope.
* **Signing can always fall back to paper** — :func:`record_paper_signature` records a completed
  signature manually (``is_paper``), so a property that requires signatures can still activate a
  lease when the provider is unavailable.

All timestamps are Africa/Johannesburg (the business timezone). Provider credentials never appear in
any log line written here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import (
    ESIGN_TERMINAL_STATUSES,
    ESIGN_WEBHOOK_EVENT_STATUS,
    EsignEnvelopeStatus,
    EsignProviderKind,
    EsignWebhookEvent,
    SecurityAuditEvent,
    SecurityAuditOutcome,
)
from src.commons.exceptions import EsignEnvelopeStateError
from src.core.config import get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import Document, EsignEnvelope, EsignEvent
from src.modules.documents import service as documents_service
from src.modules.documents.enums import (
    DocumentContentType,
    DocumentOwnerType,
    DocumentType,
    RetentionClass,
)
from src.modules.documents.esign import EsignProvider, EsignSendError
from src.modules.documents.schemas import EsignEnvelopeRead
from src.modules.documents.storage import LocalObjectStorage
from src.modules.documents.virus_scan import DocumentScanner

logger = logging.getLogger(__name__)

# Stable list sort: newest first, id as the deterministic tie-breaker so pagination never drops or
# repeats a row across pages.
_LIST_ORDER = (EsignEnvelope.created_at.desc(), EsignEnvelope.id)

# Terminal-outcome audit events, keyed by the status the envelope reached.
_STATUS_AUDIT_EVENT: dict[EsignEnvelopeStatus, SecurityAuditEvent] = {
    EsignEnvelopeStatus.SIGNED: SecurityAuditEvent.ESIGN_ENVELOPE_SIGNED,
    EsignEnvelopeStatus.DECLINED: SecurityAuditEvent.ESIGN_ENVELOPE_DECLINED,
    EsignEnvelopeStatus.EXPIRED: SecurityAuditEvent.ESIGN_ENVELOPE_EXPIRED,
}


class EsignWebhookOutcome(StrEnum):
    """What :func:`handle_webhook` did with a callback, so the router can map it to a status code.

    - ``UNKNOWN``: no envelope carries the given provider id (router -> 404).
    - ``IGNORED``: a non-terminal provider event we do not act on (router -> 200, accepted).
    - ``DUPLICATE``: the event id was already processed — an idempotent replay (router -> 200).
    - ``NOOP``: the envelope was already terminal, so nothing changed (router -> 200).
    - ``APPLIED``: a terminal outcome was applied and committed (router -> 200).
    - ``RETRY``: the provider could not return the executed artefacts; the caller should retry the
      webhook later (router -> 503) — nothing was recorded, so the retry is not deduplicated.
    """

    UNKNOWN = "unknown"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    NOOP = "noop"
    APPLIED = "applied"
    RETRY = "retry"


@dataclass(frozen=True)
class WebhookResult:
    """The outcome of handling one webhook plus the envelope it referred to (when found)."""

    outcome: EsignWebhookOutcome
    envelope: EsignEnvelope | None = None


def _now() -> datetime:
    """Return the current time in the project business timezone (Africa/Johannesburg)."""
    return datetime.now(APP_TIMEZONE)


# --------------------------------------------------------------------------------------
# Mappers — ORM rows to API read models.
# --------------------------------------------------------------------------------------


def envelope_to_read(row: EsignEnvelope) -> EsignEnvelopeRead:
    """Map an ``EsignEnvelope`` ORM row to its API read model (never the provider secret)."""
    return EsignEnvelopeRead(
        id=row.id,
        provider=EsignProviderKind(row.provider),
        owner_type=DocumentOwnerType(row.owner_type),
        owner_id=row.owner_id,
        source_document_id=row.source_document_id,
        signed_document_id=row.signed_document_id,
        certificate_document_id=row.certificate_document_id,
        status=EsignEnvelopeStatus(row.status),
        recipient_email=row.recipient_email,
        recipient_name=row.recipient_name,
        subject=row.subject,
        is_paper=bool(row.is_paper),
        sent_at=row.sent_at,
        completed_at=row.completed_at,
        expires_at=row.expires_at,
        decline_reason=row.decline_reason,
        created_at=row.created_at,
        modified_at=row.modified_at,
    )


# --------------------------------------------------------------------------------------
# Reads.
# --------------------------------------------------------------------------------------


def get_active_envelope(db: Session, envelope_id: str) -> EsignEnvelope | None:
    """Load a single non-deleted envelope by id, or ``None`` when absent/soft-deleted."""
    return db.execute(
        select(EsignEnvelope).where(
            EsignEnvelope.id == envelope_id,
            EsignEnvelope.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def get_envelope_by_provider_id(
    db: Session, provider_envelope_id: str
) -> EsignEnvelope | None:
    """Load a non-deleted envelope by the provider's envelope id, or ``None`` — the webhook lookup."""
    return db.execute(
        select(EsignEnvelope).where(
            EsignEnvelope.provider_envelope_id == provider_envelope_id,
            EsignEnvelope.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def list_envelopes(
    db: Session,
    *,
    owner_type: DocumentOwnerType,
    owner_id: str,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[EsignEnvelope], int]:
    """Return a page of one owner's non-deleted envelopes plus the total (newest first)."""
    filters = (
        EsignEnvelope.is_deleted.is_(False),
        EsignEnvelope.owner_type == owner_type.value,
        EsignEnvelope.owner_id == owner_id,
    )
    total = int(
        db.execute(
            select(func.count()).select_from(EsignEnvelope).where(*filters)
        ).scalar_one()
    )
    rows = (
        db.execute(
            select(EsignEnvelope)
            .where(*filters)
            .order_by(*_LIST_ORDER)
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def has_completed_signature(
    db: Session, *, owner_type: DocumentOwnerType, owner_id: str
) -> bool:
    """Return whether a ``signed`` envelope (electronic or paper) exists for the owner.

    The predicate the lease-activation gate reads: a property that requires signatures may only
    activate a lease once it carries a completed signature. Any non-deleted envelope in the
    ``signed`` status counts — including one recorded on paper (:func:`record_paper_signature`), so
    provider downtime never permanently blocks activation.
    """
    return (
        db.execute(
            select(EsignEnvelope.id).where(
                EsignEnvelope.owner_type == owner_type.value,
                EsignEnvelope.owner_id == owner_id,
                EsignEnvelope.status == EsignEnvelopeStatus.SIGNED.value,
                EsignEnvelope.is_deleted.is_(False),
            )
        ).first()
        is not None
    )


# --------------------------------------------------------------------------------------
# Send — hand a generated document to the provider for signature.
# --------------------------------------------------------------------------------------


def send_for_signature(
    db: Session,
    storage: LocalObjectStorage,
    provider: EsignProvider,
    *,
    document: Document,
    recipient_email: str,
    recipient_name: str,
    subject: str | None = None,
    created_by: str | None = None,
    now: datetime | None = None,
) -> EsignEnvelope:
    """Send a generated lease/addendum ``document`` for signature and record the ``sent`` envelope.

    The bytes are read from the document store and handed to the provider; on success a ``sent``
    envelope is committed, correlated by the provider's envelope id and stamped with the signing
    window. The envelope's owner is inherited from the document (a ``lease``), so the activation
    gate can find it. A provider outage raises :class:`~src.modules.documents.esign.EsignSendError`
    with **nothing persisted**, so the caller can fall back to paper.

    Raises:
        EsignSendError: The provider rejected or failed to accept the envelope (nothing persisted).
    """
    moment = now or _now()
    data = documents_service.read_document_bytes(storage, document)

    # Send first, persist second: a provider failure leaves no dangling row, and the row we do
    # write already carries the provider's envelope id the webhook will correlate against.
    provider_envelope_id = provider.send(
        document=data,
        filename=document.original_filename,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        subject=subject or document.original_filename,
    )

    expires_at = moment + timedelta(days=get_settings().esign_envelope_expire_days)
    envelope = EsignEnvelope(
        provider=provider.kind.value,
        provider_envelope_id=provider_envelope_id,
        owner_type=document.owner_type,
        owner_id=document.owner_id,
        source_document_id=document.id,
        status=EsignEnvelopeStatus.SENT.value,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        subject=subject,
        sent_at=moment,
        expires_at=expires_at,
        created_by=created_by,
    )
    db.add(envelope)
    db.commit()
    db.refresh(envelope)
    _log_event(
        SecurityAuditEvent.ESIGN_ENVELOPE_SENT,
        actor=created_by,
        envelope=envelope,
    )
    return envelope


# --------------------------------------------------------------------------------------
# Webhook — verified upstream, applied idempotently here.
# --------------------------------------------------------------------------------------


def handle_webhook(
    db: Session,
    storage: LocalObjectStorage,
    scanner: DocumentScanner,
    provider: EsignProvider,
    *,
    provider_envelope_id: str,
    event_type: str,
    provider_event_id: str,
    decline_reason: str | None = None,
    now: datetime | None = None,
) -> WebhookResult:
    """Apply a provider webhook to its envelope, idempotently. Signature is verified by the caller.

    Resolves the envelope by the provider's id, records the provider's event id (unique, so a
    replay is skipped), and applies the terminal outcome: a ``completed`` event stores the executed
    document and its certificate and marks the envelope ``signed``; ``declined`` / ``expired`` close
    it. Non-terminal events are accepted and ignored; a callback onto an already-terminal envelope
    is a no-op. If the provider cannot return the executed artefacts, nothing is recorded and the
    result is :attr:`EsignWebhookOutcome.RETRY` so the provider's retry is not deduplicated.

    The caller owns the transaction commit for an applied outcome.
    """
    moment = now or _now()
    envelope = get_envelope_by_provider_id(db, provider_envelope_id)
    if envelope is None:
        return WebhookResult(EsignWebhookOutcome.UNKNOWN)

    try:
        event = EsignWebhookEvent(event_type)
    except ValueError:
        # A provider event we do not act on (delivered, viewed, …) — accepted, not an error.
        return WebhookResult(EsignWebhookOutcome.IGNORED, envelope)

    # Record the event id first: the unique constraint is the idempotency point, so a re-delivered
    # webhook (providers deliver at-least-once) trips it here rather than re-applying the outcome.
    db.add(
        EsignEvent(
            envelope_id=envelope.id,
            provider_event_id=provider_event_id,
            event_type=event.value,
            received_at=moment,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return WebhookResult(EsignWebhookOutcome.DUPLICATE, envelope)

    if EsignEnvelopeStatus(envelope.status) in ESIGN_TERMINAL_STATUSES:
        # A distinct-but-late event onto an already-closed envelope: keep the recorded event for
        # the audit trail but change nothing.
        db.commit()
        return WebhookResult(EsignWebhookOutcome.NOOP, envelope)

    target = ESIGN_WEBHOOK_EVENT_STATUS[event]
    try:
        _apply_terminal_outcome(
            db,
            storage,
            scanner,
            provider,
            envelope=envelope,
            target=target,
            decline_reason=decline_reason,
            now=moment,
        )
    except EsignSendError:
        # The provider could not hand back the executed artefacts. Roll back the event record too,
        # so the provider's retry is processed fresh rather than deduplicated as already-seen.
        db.rollback()
        logger.warning(
            "E-signature completion deferred for envelope %s: provider artefacts unavailable.",
            envelope.id,
        )
        return WebhookResult(EsignWebhookOutcome.RETRY, envelope)

    db.commit()
    db.refresh(envelope)
    _log_event(
        _STATUS_AUDIT_EVENT[target],
        actor=None,
        envelope=envelope,
    )
    return WebhookResult(EsignWebhookOutcome.APPLIED, envelope)


def _apply_terminal_outcome(
    db: Session,
    storage: LocalObjectStorage,
    scanner: DocumentScanner,
    provider: EsignProvider,
    *,
    envelope: EsignEnvelope,
    target: EsignEnvelopeStatus,
    decline_reason: str | None,
    now: datetime,
) -> None:
    """Move ``envelope`` to a terminal status; on ``signed`` retain the executed docs (in-tx)."""
    if target is EsignEnvelopeStatus.SIGNED:
        _retain_completed_documents(
            db, storage, scanner, provider=provider, envelope=envelope, now=now
        )
    if target is EsignEnvelopeStatus.DECLINED:
        envelope.decline_reason = decline_reason
    envelope.status = target.value
    envelope.completed_at = now


def _retain_completed_documents(
    db: Session,
    storage: LocalObjectStorage,
    scanner: DocumentScanner,
    provider: EsignProvider,
    *,
    envelope: EsignEnvelope,
    now: datetime,
) -> None:
    """Fetch and store the signed document and its certificate; link both onto ``envelope``.

    Both are stored as ``permanent`` records owned by the same lease as the envelope, so they are
    reachable through the document store's signed-link download and never auto-purged. Linking them
    onto the envelope happens in the same transaction as the status flip, so a ``signed`` envelope
    always has both documents and neither can exist without the envelope recording it.
    """
    source = documents_service.get_active_document(db, envelope.source_document_id)
    source_bytes = (
        documents_service.read_document_bytes(storage, source)
        if source is not None
        else b""
    )
    artifacts = provider.fetch_completed(
        provider_envelope_id=envelope.provider_envelope_id or "",
        source_document=source_bytes,
        recipient_email=envelope.recipient_email,
        recipient_name=envelope.recipient_name,
        completed_at=now,
    )
    signed = documents_service.ingest_document(
        db,
        storage,
        scanner,
        owner_type=DocumentOwnerType(envelope.owner_type),
        owner_id=envelope.owner_id,
        document_type=DocumentType.SIGNED_LEASE,
        retention_class=RetentionClass.PERMANENT,
        data=artifacts.signed_document,
        original_filename=f"signed-{envelope.owner_id}.pdf",
        content_type=DocumentContentType.PDF,
        uploaded_by=envelope.created_by,
    )
    certificate = documents_service.ingest_document(
        db,
        storage,
        scanner,
        owner_type=DocumentOwnerType(envelope.owner_type),
        owner_id=envelope.owner_id,
        document_type=DocumentType.COMPLETION_CERTIFICATE,
        retention_class=RetentionClass.PERMANENT,
        data=artifacts.certificate,
        original_filename=f"certificate-{envelope.id}.pdf",
        content_type=DocumentContentType.PDF,
        uploaded_by=envelope.created_by,
    )
    envelope.signed_document_id = signed.id
    envelope.certificate_document_id = certificate.id


# --------------------------------------------------------------------------------------
# Paper fallback and cancellation.
# --------------------------------------------------------------------------------------


def record_paper_signature(
    db: Session,
    *,
    document: Document,
    recipient_email: str,
    recipient_name: str,
    actor: str | None = None,
    now: datetime | None = None,
) -> EsignEnvelope:
    """Record a completed signature made on paper (the graceful-degradation path).

    When the provider is unavailable, a lease can still be signed on paper; this records that fact
    as a ``signed`` envelope flagged ``is_paper``, owned by the same lease as ``document`` and
    linking the generated document as the signed artefact. It counts toward
    :func:`has_completed_signature`, so a property that requires signatures can still activate the
    lease. No certificate is produced — a manual signature has no provider audit trail.
    """
    moment = now or _now()
    envelope = EsignEnvelope(
        provider=get_settings().esign_provider.value,
        provider_envelope_id=None,
        owner_type=document.owner_type,
        owner_id=document.owner_id,
        source_document_id=document.id,
        signed_document_id=document.id,
        status=EsignEnvelopeStatus.SIGNED.value,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        is_paper=True,
        completed_at=moment,
        created_by=actor,
    )
    db.add(envelope)
    db.commit()
    db.refresh(envelope)
    _log_event(
        SecurityAuditEvent.ESIGN_PAPER_SIGNATURE,
        actor=actor,
        envelope=envelope,
    )
    return envelope


def void_envelope(
    db: Session, envelope: EsignEnvelope, *, actor: str | None = None
) -> EsignEnvelope:
    """Void an in-flight envelope (``created``/``sent`` -> ``voided``); reject a terminal one.

    Raises:
        EsignEnvelopeStateError: The envelope has already reached a terminal outcome.
    """
    if EsignEnvelopeStatus(envelope.status) in ESIGN_TERMINAL_STATUSES:
        raise EsignEnvelopeStateError(envelope.id, envelope.status, "void")
    envelope.status = EsignEnvelopeStatus.VOIDED.value
    envelope.completed_at = _now()
    db.commit()
    db.refresh(envelope)
    return envelope


# --------------------------------------------------------------------------------------
# Audit logging.
# --------------------------------------------------------------------------------------


def log_webhook_rejected(*, reason: str) -> None:
    """Write a ``SECURITY_AUDIT`` line for a webhook that failed signature verification.

    Records only that a call was rejected and why (e.g. ``missing_signature``) — **never** the
    shared secret, the presented signature or the payload — so a rejected webhook is auditable
    without leaking a credential.
    """
    body: dict[str, Any] = {
        "event": SecurityAuditEvent.ESIGN_WEBHOOK_REJECTED.value,
        "outcome": SecurityAuditOutcome.FAILURE.value,
        "reason": reason,
        "timestamp": _now().isoformat(),
    }
    logger.warning(
        "SECURITY_AUDIT %s",
        json.dumps(body, sort_keys=True, default=str),
        extra={"security_audit_event": SecurityAuditEvent.ESIGN_WEBHOOK_REJECTED.value},
    )


def _log_event(
    event: SecurityAuditEvent, *, actor: str | None, envelope: EsignEnvelope
) -> None:
    """Write a ``SECURITY_AUDIT`` line for an e-signature domain action (Issue #71).

    Carries the event, a success outcome, the acting user, the envelope and its lease, and a
    Johannesburg timestamp, as a sorted-key JSON payload so log aggregators can key on the
    ``esign_*`` events. No provider credential is ever included.
    """
    body: dict[str, Any] = {
        "event": event.value,
        "outcome": SecurityAuditOutcome.SUCCESS.value,
        "actor": actor,
        "envelope_id": envelope.id,
        "owner_type": envelope.owner_type,
        "owner_id": envelope.owner_id,
        "is_paper": bool(envelope.is_paper),
        "timestamp": _now().isoformat(),
    }
    logger.info(
        "SECURITY_AUDIT %s",
        json.dumps(body, sort_keys=True, default=str),
        extra={"security_audit_event": event.value},
    )
