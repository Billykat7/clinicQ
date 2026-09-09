"""HTTP routes for the e-signature integration — sign a lease inside the product (Issue #71).

The signing flow keeps the most legally sensitive artefact in the system inside the product: a
generated lease/addendum is sent for signature, its envelope tracked, and the executed document plus
its certificate retained on the unified document store (Issue #70). It is manager state, so the
authenticated routes reuse the ``leases`` RBAC verbs (a signature is part of a lease's lifecycle) —
no new permission resource is introduced.

Two design points carry the issue's guarantees:

* **The webhook takes no session.** A provider cannot present a user token, so ``POST /esign/webhook``
  is authenticated by an **HMAC-SHA256 signature over the raw request body** (``X-Esign-Signature``)
  and is idempotent on replay. It 404s unless a secret is configured, so the endpoint's existence is
  never revealed, and a bad signature is a 401 that logs *only* that a call was rejected — never the
  secret, the signature or the payload.
* **Signing degrades to paper.** A provider outage on send is a 503 pointing the caller at the paper
  fallback, and ``POST /esign/paper-signature`` records a manual signature so a property that requires
  signatures can still have its lease activated.

All persistence goes through ``src.modules.documents.esign_service`` so the handlers stay thin.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.api.rbac_deps import require
from src.commons.exceptions import EsignEnvelopeStateError
from src.core.config import Settings, get_settings
from src.core.scope import scoped_instance_ids
from src.core.security import get_current_user
from src.database.models import Document, EsignEnvelope
from src.database.session import get_db
from src.modules.documents import esign_service, service
from src.modules.documents.enums import DocumentOwnerType
from src.modules.documents.esign import (
    EsignSendError,
    build_esign_provider,
    verify_signature,
)
from src.modules.documents.esign_service import EsignWebhookOutcome
from src.modules.documents.schemas import (
    EsignEnvelopeListOut,
    EsignEnvelopeRead,
    EsignPaperSignatureIn,
    EsignSendIn,
    EsignWebhookIn,
)
from src.modules.documents.storage import LocalObjectStorage
from src.modules.documents.virus_scan import get_scanner

#: The owner types a document may be sent for signature as. The kernel signs a document on a
#: person's own profile; add the kinds of record your domain actually puts a signature on, so an
#: attempt to sign something unsignable is a 422 rather than a stuck envelope.
SIGNABLE_OWNER_TYPES: frozenset[DocumentOwnerType] = frozenset({DocumentOwnerType.USER})

router = APIRouter(prefix="/esign", tags=["esign"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Issue #150 (M27): these envelope endpoints physically live in the Documents module but enforce
# the ``document.signature`` resource, owned by Leases — migrated alongside Leases' own routes
# (src/modules/leases/router.py) to the generic require() factory (Issue
# #149, M26) against the leases module's manifest, replacing the named ``DocumentSignature*Dep``
# aliases that used to live in src/api/rbac_deps.py.
DocumentSignatureCreateDep = Annotated[
    None, Depends(require("document.signature", "create"))
]
# Issue #158 (M28): the read gate is the plain grant check; which envelopes a caller sees is
# decided by their grant's ``scope`` tier, resolved through the lease each envelope belongs to
# (``src.core.scope.scoped_tenant_ids``/``scoped_property_ids``).
DocumentSignatureScopedReadDep = Annotated[
    None, Depends(require("document.signature", "read"))
]
DocumentSignatureUpdateDep = Annotated[
    None, Depends(require("document.signature", "update"))
]


# --------------------------------------------------------------------------------------
# Shared helpers.
# --------------------------------------------------------------------------------------


def _storage(settings: Settings) -> LocalObjectStorage:
    """Build the private document object store from settings (envelopes reuse the document store)."""
    return LocalObjectStorage(settings.document_storage_dir)


def _ensure_subject_in_scope(db: Session, current_user: dict, subject_id: str) -> None:
    """404 unless the caller's ``document.signature`` scope covers ``subject_id``.

    An envelope has no ownership columns of its own — it belongs to the record being signed — so
    its scope *is* that record's. An unknown subject is the same 404 as an out-of-scope one, so
    this never becomes a way to probe which records exist.

    The kernel narrows on the record's own id; if the thing being signed is scoped by something
    else (its parent, its owner), resolve that here the way
    ``src.modules.documents.router._owner_identity`` does.
    """
    instance_ids = scoped_instance_ids(db, current_user, "document.signature")
    if instance_ids is None:
        return
    if subject_id not in instance_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signing subject not found.",
        )


def _current_user_id(db: Session, current_user: dict) -> str | None:
    """Resolve the acting user's id from their token email claim, or ``None``."""
    email = (current_user.get("email") or "").strip()
    return service.find_user_id_by_email(db, email) if email else None


def _load_lease_document_or_error(db: Session, document_id: str) -> Document:
    """Return a non-deleted, lease-owned document by id, or raise 404/422.

    Only a document owned by a ``lease`` may be signed: the envelope inherits that owner, and the
    lease-activation gate looks a signature up by ``(lease, lease_id)``.
    """
    document = service.get_active_document(db, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    if DocumentOwnerType(document.owner_type) not in SIGNABLE_OWNER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This kind of document cannot be sent for signature.",
        )
    return document


def _load_envelope_or_404(db: Session, envelope_id: str) -> EsignEnvelope:
    """Return a non-deleted envelope by id, or raise 404."""
    envelope = esign_service.get_active_envelope(db, envelope_id)
    if envelope is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Envelope not found."
        )
    return envelope


def _load_scoped_envelope_or_404(
    db: Session, current_user: dict, envelope_id: str
) -> EsignEnvelope:
    """Return an envelope whose lease the caller's scope covers, or 404 (Issue #178).

    An envelope has no ownership columns of its own, so its gate is its lease's
    (:func:`_ensure_lease_in_scope`). Issue #178 lifted that check out of ``get_envelope`` and onto
    every per-envelope route: the read narrowed but ``void`` did not, so a caller narrowed to one
    property could void the signature envelope of *any* lease in the business while holding only
    ``document.signature:UPDATE``. Voiding an executed-in-flight envelope is a destructive act on a
    legal artefact, which is why this one is called out here rather than left to the verb.
    """
    envelope = _load_envelope_or_404(db, envelope_id)
    _ensure_subject_in_scope(db, current_user, envelope.owner_id)
    return envelope


# --------------------------------------------------------------------------------------
# Send, list, read, void.
# --------------------------------------------------------------------------------------


@router.post(
    "/envelopes",
    response_model=EsignEnvelopeRead,
    status_code=status.HTTP_201_CREATED,
)
async def send_for_signature(
    _rbac: DocumentSignatureCreateDep,
    db: DbSession,
    settings: SettingsDep,
    current_user: CurrentUser,
    body: EsignSendIn,
) -> EsignEnvelopeRead:
    """Send a generated lease document for signature. Requires CREATE on ``leases``.

    Unavailable (503) when e-signature is disabled or the provider is down — sign on paper via
    ``POST /esign/paper-signature`` instead. The document must be lease-owned (else 404/422).
    """
    if not settings.esign_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="E-signature is disabled; record a paper signature instead.",
        )
    document = _load_lease_document_or_error(db, body.document_id)
    try:
        envelope = esign_service.send_for_signature(
            db,
            _storage(settings),
            build_esign_provider(settings),
            document=document,
            recipient_email=body.recipient_email,
            recipient_name=body.recipient_name,
            subject=body.subject,
            created_by=_current_user_id(db, current_user),
        )
    except EsignSendError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The e-signature provider is unavailable; record a paper signature instead."
            ),
        ) from exc
    return esign_service.envelope_to_read(envelope)


@router.get("/envelopes", response_model=EsignEnvelopeListOut)
async def list_envelopes(
    _rbac: DocumentSignatureScopedReadDep,
    db: DbSession,
    current_user: CurrentUser,
    owner_id: Annotated[
        str,
        Query(
            min_length=1, max_length=36, description="Owner id to list envelopes for."
        ),
    ],
    owner_type: Annotated[
        DocumentOwnerType, Query(description="Kind of entity being signed.")
    ] = DocumentOwnerType.USER,
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> EsignEnvelopeListOut:
    """List one owner's signature envelopes (paginated, newest first).

    Requires READ on ``document.signature``, and that the record being listed is inside the caller's
    own scope: an ``own``-tier caller sees their own envelopes and 404s on anyone else's, a
    ``business``-tier caller is unchanged.
    """
    _ensure_subject_in_scope(db, current_user, owner_id)
    rows, total = esign_service.list_envelopes(
        db, owner_type=owner_type, owner_id=owner_id, offset=offset, limit=limit
    )
    return EsignEnvelopeListOut(
        items=[esign_service.envelope_to_read(r) for r in rows], total=total
    )


@router.get("/envelopes/{envelope_id}", response_model=EsignEnvelopeRead)
async def get_envelope(
    _rbac: DocumentSignatureScopedReadDep,
    db: DbSession,
    current_user: CurrentUser,
    envelope_id: str,
) -> EsignEnvelopeRead:
    """Return one envelope's status, scoped through the lease it belongs to (Issue #158)."""
    return esign_service.envelope_to_read(
        _load_scoped_envelope_or_404(db, current_user, envelope_id)
    )


@router.post("/envelopes/{envelope_id}/void", response_model=EsignEnvelopeRead)
async def void_envelope(
    _rbac: DocumentSignatureUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    envelope_id: str,
) -> EsignEnvelopeRead:
    """Void an in-flight envelope. Requires UPDATE on ``leases``; 409 if already terminal."""
    envelope = _load_scoped_envelope_or_404(db, current_user, envelope_id)
    try:
        voided = esign_service.void_envelope(
            db, envelope, actor=_current_user_id(db, current_user)
        )
    except EsignEnvelopeStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return esign_service.envelope_to_read(voided)


@router.post(
    "/paper-signature",
    response_model=EsignEnvelopeRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_paper_signature(
    _rbac: DocumentSignatureUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    body: EsignPaperSignatureIn,
) -> EsignEnvelopeRead:
    """Record a signature made on paper (provider-unavailable fallback). Requires UPDATE on ``leases``.

    Always available — this is the graceful-degradation path — so a property that requires signatures
    can still have its lease activated when the provider is down.
    """
    document = _load_lease_document_or_error(db, body.document_id)
    envelope = esign_service.record_paper_signature(
        db,
        document=document,
        recipient_email=body.recipient_email,
        recipient_name=body.recipient_name,
        actor=_current_user_id(db, current_user),
    )
    return esign_service.envelope_to_read(envelope)


# --------------------------------------------------------------------------------------
# Webhook — no session; HMAC-signature verified over the raw body; idempotent.
# --------------------------------------------------------------------------------------


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def esign_webhook(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    x_esign_signature: Annotated[str | None, Header()] = None,
) -> None:
    """Apply a verified provider webhook to its envelope, idempotently.

    404 when the feature/secret is not configured (the endpoint's existence is not revealed); 401
    when the HMAC signature over the raw body does not match; 400 on an unparseable body; 404 when no
    envelope carries the provider id; 503 when the provider cannot yet return the executed artefacts
    (retry). Otherwise 204 — including for an ignored, duplicate or already-terminal callback, all of
    which are accepted no-ops.
    """
    secret = settings.esign_webhook_secret
    if not settings.esign_enabled or not secret:
        # Feature-off: do not reveal the endpoint exists when no secret is configured.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    raw = await request.body()
    if not verify_signature(raw, x_esign_signature, secret):
        esign_service.log_webhook_rejected(reason="invalid_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature"
        )

    try:
        payload = EsignWebhookIn.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed webhook body"
        ) from exc

    result = esign_service.handle_webhook(
        db,
        _storage(settings),
        get_scanner(settings),
        build_esign_provider(settings),
        provider_envelope_id=payload.provider_envelope_id,
        event_type=payload.event,
        provider_event_id=payload.event_id,
        decline_reason=payload.reason,
    )
    if result.outcome is EsignWebhookOutcome.UNKNOWN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown envelope"
        )
    if result.outcome is EsignWebhookOutcome.RETRY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider artefacts unavailable; retry later.",
        )
