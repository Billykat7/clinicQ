"""Application-wide exception types and the one error envelope every API error is sent in.

**Raising.** A service raises a domain error: a subclass of :class:`BKPropertyError`, usually of
one of the category bases below (:class:`NotFoundError`, :class:`ConflictError`, …), with a
human-readable message and a stable ``code``. It never raises ``HTTPException``: a service does not
know it is behind HTTP (a USSD session and a scheduled job call the same code).

**Answering.** ``src.core.error_handlers`` turns an uncaught domain error into its category's HTTP
status and an :class:`ErrorEnvelope` body, so a route needs no ``try``/``except`` to map one. The
same envelope carries ``HTTPException`` and request-validation errors, so a client reads one shape
for every failure:

.. code-block:: json

    {"detail": "Ticket 01a0… cannot move from done to called.",
     "code": "queue.ticket.illegal_transition",
     "request_id": "7c9e…"}

``detail`` is the field every kernel client and the static JS already read, which is why the
envelope extends it instead of replacing it. A request-validation error keeps FastAPI's list of
field errors there.

**Codes** are dotted and lowercase, ``<area>.<reason>`` (``queue.ticket.illegal_transition``). A
client branches on the code, never on the message, so a code is a contract: rename one only with
the clients that read it.
"""

from http import HTTPStatus
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ErrorEnvelope(BaseModel):
    """The JSON body of every API error response (Issue 4)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "detail": "No such ticket: 01a08ecc-dd1f-7350-9d45-9273b6e70647.",
                    "code": "queue.ticket.not_found",
                    "request_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                }
            ]
        }
    )

    detail: str | dict[str, Any] | list[dict[str, Any]] = Field(
        description=(
            "What went wrong, safe to show to the user. A request-validation error (422 "
            "`request.invalid`) carries FastAPI's list of field errors instead, and an RBAC "
            "refusal (403 `insufficient_permission`) an object naming the resource and verb it lacked."
        )
    )
    code: str = Field(
        description=(
            "Stable machine-readable error code, `<area>.<reason>`. Branch on this, never on "
            "`detail`."
        ),
        examples=["queue.ticket.not_found", "http.not_found", "request.invalid"],
    )
    request_id: str | None = Field(
        default=None,
        description="The id of the request that failed, to quote in a support message.",
    )


class BKPropertyError(Exception):
    """Base error for domain and infrastructure failures.

    Subclass one of the category bases below rather than this class directly, so the HTTP status
    is decided once per category. ``status_code`` is the status the envelope handler answers
    with; a subclass overrides it as a class attribute, never per instance.
    """

    status_code: ClassVar[HTTPStatus] = HTTPStatus.BAD_REQUEST

    def __init__(self, message: str, *, code: str = "clinicq.error") -> None:
        super().__init__(message)
        self.code = code

    def to_envelope(self, *, request_id: str | None = None) -> ErrorEnvelope:
        """Return this error as the :class:`ErrorEnvelope` the API answers with."""
        return ErrorEnvelope(detail=str(self), code=self.code, request_id=request_id)


class NotFoundError(BKPropertyError):
    """A record does not exist, or the caller may not know that it does: HTTP 404.

    Cross-site access answers 404 rather than 403 (non-negotiable 3), so an id cannot be probed
    for existence. Raise this, not :class:`ForbiddenError`, when the honest answer would leak that.
    """

    status_code = HTTPStatus.NOT_FOUND


class ConflictError(BKPropertyError):
    """The request is valid but conflicts with the record's current state: HTTP 409.

    An illegal status transition, a duplicate, a record that has already been decided.
    """

    status_code = HTTPStatus.CONFLICT


class UnprocessableError(BKPropertyError):
    """The request is well-formed but breaks a business rule about its content: HTTP 422."""

    status_code = HTTPStatus.UNPROCESSABLE_CONTENT


class ForbiddenError(BKPropertyError):
    """The caller is known but may not do this: HTTP 403."""

    status_code = HTTPStatus.FORBIDDEN


class UpstreamError(BKPropertyError):
    """A provider this service depends on (SMS, payments, e-signature) failed: HTTP 502."""

    status_code = HTTPStatus.BAD_GATEWAY


class ServiceUnavailableError(BKPropertyError):
    """The feature is not enabled or configured for this deployment: HTTP 503."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE


class InvalidImageError(UnprocessableError):
    """Raised when an uploaded unit photo cannot be decoded as a real image (Issue #24).

    The content-type allow-list is checked before this, so reaching here means the bytes
    claimed an accepted image type but Pillow could not open/re-encode them. The API layer
    maps it to HTTP 422 Unprocessable Content.
    """

    def __init__(self, message: str = "Uploaded file is not a valid image.") -> None:
        super().__init__(message, code="clinicq.photo.invalid_image")


class MessageAnchorNotFoundError(NotFoundError):
    """Raised when a messaging thread is opened against a record that does not exist (Issue #68).

    A thread is anchored to a domain record (a unit, lease, work order or application); if no such
    record exists there is nothing to anchor to. Carries the anchor kind and id so the API layer
    can build a clear message and map it to HTTP 404 Not Found.
    """

    def __init__(self, anchor_type: str, anchor_id: str) -> None:
        super().__init__(
            f"No {anchor_type} record {anchor_id} to anchor a thread to.",
            code="messaging.anchor_not_found",
        )
        self.anchor_type = anchor_type
        self.anchor_id = anchor_id


class NotAThreadParticipantError(NotFoundError):
    """Raised when a user who is not a participant acts on a thread (Issue #68).

    Participants are derived from a thread's anchor (the tenant, the owner, the assigned vendor,
    the applicant); anyone else may neither read nor post. The API layer maps this to HTTP 404 Not
    Found so a non-participant cannot even tell the thread exists.
    """

    def __init__(self, thread_id: str) -> None:
        super().__init__(
            f"Caller is not a participant of thread {thread_id}.",
            code="messaging.not_a_participant",
        )
        self.thread_id = thread_id


class MessageDraftNotFoundError(NotFoundError):
    """Raised when a message draft cannot be found for the acting author (Issue #112).

    Drafts are private to their author, so a draft owned by someone else is indistinguishable from
    one that never existed: both surface here and the API layer maps this to HTTP 404 Not Found,
    revealing nothing about another user's drafts.
    """

    def __init__(self, draft_id: str) -> None:
        super().__init__(
            f"No message draft {draft_id} for this author.",
            code="messaging.draft_not_found",
        )
        self.draft_id = draft_id


class AlertDraftNotFoundError(NotFoundError):
    """Raised when an alert draft cannot be found for the acting author (Issue #132 follow-up).

    Mirrors :class:`MessageDraftNotFoundError`: a draft owned by someone else is indistinguishable
    from one that never existed, both surfacing here and mapped to HTTP 404 Not Found.
    """

    def __init__(self, draft_id: str) -> None:
        super().__init__(
            f"No alert draft {draft_id} for this author.",
            code="alerts.draft_not_found",
        )
        self.draft_id = draft_id


class AudienceNotAuthorisedError(ForbiddenError):
    """Raised when a sender targets an announcement audience outside their authority (Issue #112).

    An audience resolves from RBAC + ownership: an admin alone may broadcast globally, an owner
    only to their own tenants or a property they own, a manager only within their managed scope.
    Targeting anything else — a property that is not theirs, a global broadcast without admin —
    raises this, which the API layer maps to HTTP 403 Forbidden. Carries the attempted audience
    kind (and reference, when any) so the message is specific without leaking who *is* in scope.
    """

    def __init__(self, audience_type: str, audience_ref: str | None = None) -> None:
        target = f" {audience_ref}" if audience_ref else ""
        super().__init__(
            f"Not authorised to send to the {audience_type}{target} audience.",
            code="messaging.audience_not_authorised",
        )
        self.audience_type = audience_type
        self.audience_ref = audience_ref


class EmptyAudienceError(ConflictError):
    """Raised when a resolved announcement audience contains no reachable recipient (Issue #112).

    The sender is authorised, but the audience resolves to an empty set (e.g. an owner with no
    account-linked tenants yet). There is no one to broadcast to, so rather than create an empty
    thread the API layer maps this to HTTP 409 Conflict with a clear message.
    """

    def __init__(self, audience_type: str) -> None:
        super().__init__(
            f"The {audience_type} audience resolves to no reachable recipients.",
            code="messaging.audience_empty",
        )
        self.audience_type = audience_type


class PeerNotReachableError(ForbiddenError):
    """Raised when a tenant tries to message someone they do not share a property with (Issue #126).

    Peer-to-peer messaging is authorised by a server-checked **co-occupancy** relationship: the
    sender and recipient must each hold an active tenancy in the same property. A recipient who is
    not a reachable neighbour — including one who does not exist or has no linked account — raises
    this, which the API layer maps to HTTP 403 Forbidden with a message that reveals nothing about
    whether the target exists or where they live (the negative case must never leak the directory).
    """

    def __init__(self) -> None:
        super().__init__(
            "You can only message a tenant of a property you share.",
            code="messaging.peer_not_reachable",
        )


class InAppNotificationNotFoundError(NotFoundError):
    """Raised when an in-app notification cannot be found for the acting user (Issue #113).

    Notifications are private to the user they belong to, so one owned by someone else is
    indistinguishable from one that never existed: both surface here and the API layer maps this to
    HTTP 404 Not Found, revealing nothing about another user's feed.
    """

    def __init__(self, notification_id: str) -> None:
        super().__init__(
            f"No in-app notification {notification_id} for this user.",
            code="notifications.in_app_not_found",
        )
        self.notification_id = notification_id


class DocumentInfectedError(UnprocessableError):
    """Raised when a document upload fails the virus scan on ingest (Issue #70).

    Scanning runs on the raw bytes before the document row is committed, so an infected upload is
    refused at the door rather than stored and later served. Carries the scanner's verdict so the
    API layer can build a clear message and map it to HTTP 422 Unprocessable Content, with nothing
    written to storage or the database.
    """

    def __init__(self, *, filename: str, scan_status: str) -> None:
        super().__init__(
            f"Document {filename!r} failed the virus scan (status: {scan_status}) and was rejected.",
            code="documents.infected",
        )
        self.filename = filename
        self.scan_status = scan_status


class DocumentChecksumMismatchError(ConflictError):
    """Raised when a stored document's bytes no longer match its recorded checksum (Issue #70).

    Every document records the SHA-256 of the bytes written at ingest; the download path re-hashes
    the bytes it reads and compares. A mismatch means the object was corrupted or tampered with out
    of band, so the download **fails loudly** rather than serving bad bytes — the API layer maps it
    to HTTP 409 Conflict. Carries the document id and both digests for the audit trail.
    """

    def __init__(self, document_id: str, *, expected: str, actual: str) -> None:
        super().__init__(
            f"Document {document_id} failed its integrity check: stored bytes do not match the "
            "recorded checksum.",
            code="documents.checksum_mismatch",
        )
        self.document_id = document_id
        self.expected = expected
        self.actual = actual


class EsignEnvelopeStateError(ConflictError):
    """Raised when an action is attempted on an envelope in the wrong state (Issue #71).

    Sending an already-sent envelope, or voiding one that has already reached a terminal outcome
    (signed / declined / expired), is rejected with the record left untouched. Carries the current
    status and the attempted action so the API layer can build a clear message and map it to HTTP
    409 Conflict.
    """

    def __init__(self, envelope_id: str, current_status: str, action: str) -> None:
        super().__init__(
            f"E-signature envelope {envelope_id} cannot {action} from status {current_status}.",
            code="esign.illegal_state",
        )
        self.envelope_id = envelope_id
        self.current_status = current_status
        self.action = action


class StripeNotConfiguredError(ServiceUnavailableError):
    """Raised when a Stripe operation is attempted while the gateway is disabled (Issue #76).

    Creating a PaymentIntent or handling a webhook needs ``STRIPE_ENABLED`` set with keys present;
    when the gateway is off there is nothing to talk to. The API layer maps this to HTTP 503
    Service Unavailable — the feature is simply not turned on for this deployment (manual capture
    stays available as the fallback).
    """

    def __init__(self) -> None:
        super().__init__(
            "The Stripe payment gateway is not enabled for this deployment.",
            code="payments.stripe_disabled",
        )


class StripeSignatureError(BKPropertyError):
    """Raised when a Stripe webhook fails signature verification (Issue #76).

    An unsigned webhook, one signed with the wrong secret, or a replayed-and-tampered payload never
    reaches the ledger: the signature is checked before the event is parsed. The API layer maps this
    to HTTP 400 Bad Request so a forged call is rejected without side effects.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Stripe webhook signature verification failed: {reason}.",
            code="payments.stripe_bad_signature",
        )
        self.reason = reason


class PaystackNotConfiguredError(ServiceUnavailableError):
    """Raised when a Paystack operation is attempted while the gateway is disabled (Issue #79).

    Initializing a transaction or handling a webhook needs ``PAYSTACK_ENABLED`` set with the secret
    key present; when the gateway is off there is nothing to talk to. The API layer maps this to HTTP
    503 Service Unavailable — the feature is simply not turned on for this deployment (manual capture
    stays available as the fallback).
    """

    def __init__(self) -> None:
        super().__init__(
            "The Paystack payment gateway is not enabled for this deployment.",
            code="payments.paystack_disabled",
        )


class PaystackSignatureError(BKPropertyError):
    """Raised when a Paystack webhook fails signature verification (Issue #79).

    An unsigned webhook, one signed with the wrong key, or a tampered payload never reaches the
    ledger: the ``x-paystack-signature`` HMAC-SHA512 over the raw body is checked before the event is
    parsed. The API layer maps this to HTTP 400 Bad Request so a forged call is rejected without side
    effects.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Paystack webhook signature verification failed: {reason}.",
            code="payments.paystack_bad_signature",
        )
        self.reason = reason
