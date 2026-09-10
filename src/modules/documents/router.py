"""HTTP routes for the document storage service — one store for every file (Issue #70).

This is the platform surface that consolidates tenant, application, lease and maintenance
attachments behind one ``document`` record. It is a manager-side context: an authorised user ingests
a file against a polymorphic owner, lists that owner's documents, mints a short-lived signed link,
and downloads the bytes only through the link.

Access control is **inherited from the owner's domain**: rather than a new ``documents`` RBAC
resource, each route enforces the verb on the resource that governs the document's owner type (see
``OWNER_TYPE_RESOURCE``) — so a caller can reach a document exactly when they can reach the thing it
is attached to. Ingest additionally scans the bytes and refuses anything infected (422); a mismatch
between the stored bytes and their recorded checksum fails the download loudly (409). An upload
outside the allow-list is 415 and over the cap is 413; deletes are soft and purge the bytes.

All persistence goes through ``src.modules.documents.service`` so the handlers stay thin.
"""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from src.commons.enums import PermissionVerb
from src.commons.exceptions import (
    DocumentChecksumMismatchError,
    DocumentInfectedError,
)
from src.commons.http import DownloadDisposition, content_disposition_header
from src.core.config import Settings, get_settings
from src.core.rate_limit_deps import SignedLinkMintLimit
from src.core.rbac import ensure_permission_key
from src.core.scope import scoped_instance_ids
from src.core.security import get_current_user
from src.database.models import Document
from src.database.session import get_db
from src.modules.documents import service
from src.modules.documents.download_links import decode_document_download_token
from src.modules.documents.enums import (
    OWNER_TYPE_RESOURCE,
    DocumentContentType,
    DocumentOwnerType,
    DocumentType,
    RetentionClass,
)
from src.modules.documents.schemas import (
    DocumentLinkOut,
    DocumentListOut,
    DocumentRead,
)
from src.modules.documents.storage import LocalObjectStorage
from src.modules.documents.virus_scan import get_scanner

router = APIRouter(prefix="/documents", tags=["documents"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Read uploads in bounded chunks so an oversized body is rejected without ever holding the whole file
# in memory (see :func:`_read_upload_within_cap`).
_UPLOAD_CHUNK_BYTES = 64 * 1024


@router.get("/info", summary="Module metadata", operation_id="documentsInfo")
def documents_info() -> dict[str, str]:
    """Return stub documents module metadata."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


# --------------------------------------------------------------------------------------
# Shared helpers.
# --------------------------------------------------------------------------------------


def _storage(settings: Settings) -> LocalObjectStorage:
    """Build the private document object store from settings."""
    return LocalObjectStorage(settings.document_storage_dir)


def _actor_label(current_user: dict) -> str | None:
    """A stable identifier for the caller (email, else ``sub``) for audit/link attribution."""
    return current_user.get("email") or current_user.get("sub")


def _current_user_id(db: Session, current_user: dict) -> str | None:
    """Resolve the acting user's id from their token email claim, or ``None``."""
    email = (current_user.get("email") or "").strip()
    return service.find_user_id_by_email(db, email) if email else None


def _owner_identity(
    db: Session, owner_type: DocumentOwnerType, owner_id: str
) -> str | None:
    """Resolve the scope instance a document's owner sits under, or ``None`` if unknown.

    The axis :mod:`src.core.scope` narrows by, read off whichever row the document hangs from. An
    owner that no longer resolves yields ``None``, which :func:`_assert_owner_in_scope` treats as
    out of scope for any narrowed caller — fail closed, so a dangling document is unreachable
    rather than universally reachable.

    A file on a person's own profile is scoped by that person, so the owner id *is* the instance.
    Add a branch per owner type you introduce; return the id of whatever
    :func:`~src.core.scope.instance_ids_in_scope` narrows on for that record.
    """
    match owner_type:
        case DocumentOwnerType.USER:
            return owner_id
        case _:
            return None


def _assert_owner_in_scope(
    db: Session, current_user: dict, owner_type: DocumentOwnerType, owner_id: str
) -> None:
    """404 unless the caller's scope covers the entity the document is attached to (Issue #178).

    A document is only as reachable as the thing it is attached to — that is what
    :data:`OWNER_TYPE_RESOURCE` says — but the verb on that resource is only half of it. Without
    this, a caller narrowed to a handful of instances could list, read, mint a signed link for and
    delete every document in the business while holding nothing the owning module would have let
    them near. This applies the same instance filter that module's own detail route applies.

    **404, not 403**: a document a caller may not reach must be indistinguishable from one that
    does not exist, or the error itself enumerates what is there.
    """
    resource_key = OWNER_TYPE_RESOURCE[owner_type]
    instance_ids = scoped_instance_ids(db, current_user, resource_key)
    if instance_ids is None:
        return
    if _owner_identity(db, owner_type, owner_id) not in instance_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )


def _authorize(
    db: Session,
    current_user: dict,
    owner_type: DocumentOwnerType,
    owner_id: str,
    verb: PermissionVerb,
) -> None:
    """Enforce ``verb`` **and** the owner's scope on the resource that governs ``owner_type``.

    A document inherits its access control from the domain it is attached to, so this maps the
    owner type to that domain's resource key and defers to :func:`ensure_permission_key` (401
    unauthenticated, 403 under-privileged) — then, per Issue #178, to
    :func:`_assert_owner_in_scope` for the row-level half the verb alone never answered.
    """
    ensure_permission_key(db, current_user, OWNER_TYPE_RESOURCE[owner_type], verb.value)
    _assert_owner_in_scope(db, current_user, owner_type, owner_id)


def _load_document_or_404(db: Session, document_id: str) -> Document:
    """Return a non-deleted document by id, or raise 404."""
    row = service.get_active_document(db, document_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    return row


def _require_content_type(raw: str | None) -> DocumentContentType:
    """Return the accepted content type for an upload, or raise 415."""
    media_type = (raw or "").split(";", 1)[0].strip().lower()
    try:
        return DocumentContentType(media_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported document type; allowed: application/pdf, image/jpeg, image/png, "
                "image/webp, video/mp4, video/webm."
            ),
        ) from exc


async def _read_upload_within_cap(
    file: UploadFile, cap_bytes: int, declared_length: int | None
) -> bytes:
    """Read an upload into memory only if it stays within ``cap_bytes``, else raise 413."""
    if declared_length is not None and declared_length > cap_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Document exceeds the maximum size of {cap_bytes} bytes.",
        )
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > cap_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Document exceeds the maximum size of {cap_bytes} bytes.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _download_url(request: Request, token: str) -> str:
    """Build the absolute, host-agnostic signed download URL carrying ``token``."""
    return str(request.url_for("download_document").include_query_params(token=token))


# --------------------------------------------------------------------------------------
# Ingest, list, read.
# --------------------------------------------------------------------------------------


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def ingest_document(
    db: DbSession,
    settings: SettingsDep,
    current_user: CurrentUser,
    owner_type: Annotated[
        DocumentOwnerType, Form(description="Kind of entity the document belongs to.")
    ],
    owner_id: Annotated[
        str, Form(min_length=1, max_length=36, description="Id of the owning entity.")
    ],
    document_type: Annotated[DocumentType, Form(description="What the document is.")],
    file: Annotated[
        UploadFile, File(description="The document bytes (pdf/image/video).")
    ],
    retention_class: Annotated[
        RetentionClass, Form(description="How long the document is kept.")
    ] = RetentionClass.STANDARD,
    content_length: Annotated[int | None, Header()] = None,
) -> DocumentRead:
    """Ingest a document against a polymorphic owner: scan, checksum and store it privately.

    Requires CREATE on the owner type's resource. Enforces the content-type allow-list (415) and the
    size cap (413, before buffering), scans the bytes and refuses anything infected (422), then
    records the SHA-256 and stores the bytes verbatim under an opaque private key.
    """
    _authorize(db, current_user, owner_type, owner_id, PermissionVerb.CREATE)
    content_type = _require_content_type(file.content_type)
    data = await _read_upload_within_cap(
        file, settings.document_max_bytes, content_length
    )
    try:
        row = service.ingest_document(
            db,
            _storage(settings),
            get_scanner(settings),
            owner_type=owner_type,
            owner_id=owner_id,
            document_type=document_type,
            retention_class=retention_class,
            data=data,
            original_filename=file.filename or "document",
            content_type=content_type,
            uploaded_by=_current_user_id(db, current_user),
        )
    except DocumentInfectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return service.document_to_read(row)


@router.get("", response_model=DocumentListOut)
async def list_documents(
    db: DbSession,
    current_user: CurrentUser,
    owner_type: Annotated[
        DocumentOwnerType, Query(description="Kind of entity to list documents for.")
    ],
    owner_id: Annotated[
        str, Query(min_length=1, max_length=36, description="Id of the owning entity.")
    ],
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> DocumentListOut:
    """List one owner's documents (paginated, newest first). Requires READ on the owner's resource."""
    _authorize(db, current_user, owner_type, owner_id, PermissionVerb.READ)
    rows, total = service.list_documents(
        db, owner_type=owner_type, owner_id=owner_id, offset=offset, limit=limit
    )
    return DocumentListOut(
        items=[service.document_to_read(r) for r in rows], total=total
    )


@router.get("/download", name="download_document")
async def download_document(
    db: DbSession,
    settings: SettingsDep,
    token: Annotated[
        str, Query(description="Signed, single-document-scoped download token.")
    ],
    disposition: Annotated[
        DownloadDisposition,
        Query(
            description="``attachment`` to download (default), ``inline`` to preview in the browser."
        ),
    ] = "attachment",
) -> Response:
    """Stream a document's bytes for a valid signed link, verifying integrity and auditing the read.

    Takes no session: authorisation is the short-lived signed ``token`` alone, scoped to one document
    and recording the actor it was minted for. An invalid/expired/tampered token is a 403; a valid
    token to a missing document is a 404; bytes that no longer match the recorded checksum are a 409
    (the download fails loudly rather than serving corrupted content).

    ``disposition`` selects save-to-disk (``attachment``) or in-app preview (``inline``) from the same
    bytes and the same signed link — both are audited identically (Issue #99).

    Declared before ``/{document_id}`` so the static path is matched first, not captured as an id.
    """
    decoded = decode_document_download_token(token)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired download link.",
        )
    document_id, actor = decoded
    document = service.get_active_document(db, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    try:
        data = service.read_document_bytes(_storage(settings), document)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        ) from exc
    except DocumentChecksumMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    service.log_download(document, actor=actor)
    return Response(
        content=data,
        media_type=document.content_type,
        headers={
            "Content-Disposition": content_disposition_header(
                disposition, document.original_filename
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    db: DbSession,
    current_user: CurrentUser,
    document_id: str,
) -> DocumentRead:
    """Return one document's metadata. Requires READ on the document owner's resource."""
    row = _load_document_or_404(db, document_id)
    _authorize(
        db,
        current_user,
        DocumentOwnerType(row.owner_type),
        row.owner_id,
        PermissionVerb.READ,
    )
    return service.document_to_read(row)


@router.post("/{document_id}/link", response_model=DocumentLinkOut)
async def create_document_link(
    db: DbSession,
    _mint_limit: SignedLinkMintLimit,
    current_user: CurrentUser,
    request: Request,
    document_id: str,
) -> DocumentLinkOut:
    """Mint a short-lived signed download link for a document. Requires READ on the owner's resource."""
    row = _load_document_or_404(db, document_id)
    _authorize(
        db,
        current_user,
        DocumentOwnerType(row.owner_type),
        row.owner_id,
        PermissionVerb.READ,
    )
    token, expires_at = service.create_download_link(
        row, actor=_actor_label(current_user)
    )
    return DocumentLinkOut(
        document_id=row.id,
        download_url=_download_url(request, token),
        expires_at=expires_at,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    db: DbSession,
    settings: SettingsDep,
    current_user: CurrentUser,
    document_id: str,
) -> None:
    """Soft-delete a document and purge its bytes. Requires DELETE on the document owner's resource."""
    row = _load_document_or_404(db, document_id)
    _authorize(
        db,
        current_user,
        DocumentOwnerType(row.owner_type),
        row.owner_id,
        PermissionVerb.DELETE,
    )
    service.soft_delete_document(db, _storage(settings), row)
