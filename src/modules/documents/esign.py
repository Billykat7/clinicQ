"""E-signature provider interface, implementations and webhook verification (Issue #71).

A lease is the most legally sensitive artefact in the system, so the signing provider sits behind a
small interface — :class:`EsignProvider` — and never leaks into the domain. Swapping DocuSign for
Dropbox Sign, or dropping to paper when the provider is down, is then a matter of configuration, not
a code change through the leases module.

Two implementations ship here, mirroring how the SMS channel and the virus scanner are pluggable:

* :class:`LocalEsignProvider` — the default. A self-contained provider that creates an envelope,
  returns a synthetic id and produces a signed document plus a real certificate-of-completion PDF
  without any external account, so the whole flow runs end-to-end in development and CI (the
  ``SMTP_HOST``-unset / EICAR analogue). It signs a simulated webhook with the same HMAC primitive a
  real gateway uses, so verification and idempotency are exercised for real.
* :class:`FakeEsignProvider` — an in-memory test double the suite asserts against and can drive to
  fail on demand (to exercise the graceful-degradation / paper-signature path).

Webhook authenticity is an **HMAC-SHA256 signature over the raw request body**, keyed on the shared
secret (:func:`sign_payload` / :func:`verify_signature`) — the industry-standard scheme every major
provider uses, and stronger than a bare shared-secret header because it also binds the body. The
secret is a credential and is **never logged**.

:func:`build_esign_provider` selects the implementation from settings.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from src.commons.enums import EsignProviderKind
from src.core.config import Settings, get_settings
from src.modules.documents.pdf_theme import (
    DocumentMeta,
    StampKind,
    render_lines_pdf,
)

logger = logging.getLogger(__name__)


class EsignSendError(Exception):
    """Raised when a provider fails to accept or return a signing envelope.

    Treated as *provider downtime* by the service: the send is not retried in-band, and a property
    that requires signatures can still have the lease signed on paper (the graceful-degradation
    path), so a provider outage never blocks a lease indefinitely.
    """


@dataclass(frozen=True)
class CompletedArtifacts:
    """The executed document and its certificate of completion, returned on completion.

    Both are retained on the unified document store so the signature is provable long after the
    fact — the signed document *and* the provider's audit certificate.
    """

    signed_document: bytes
    certificate: bytes


class EsignProvider(ABC):
    """Interface every e-signature gateway implementation satisfies.

    Two operations: hand a document to the provider for signature (returning the provider's
    envelope id, used later to correlate a webhook), and retrieve the executed artefacts once the
    envelope completes. Both raise :class:`EsignSendError` on any failure so the service can fall
    back to paper.
    """

    #: Stable provider identifier recorded on the envelope row (``esign_envelope.provider``).
    kind: EsignProviderKind

    @abstractmethod
    def send(
        self,
        *,
        document: bytes,
        filename: str,
        recipient_email: str,
        recipient_name: str,
        subject: str,
    ) -> str:
        """Create a signing envelope for ``document`` and return the provider's envelope id.

        Args:
            document: The generated lease/addendum PDF bytes to be signed.
            filename: The document's filename (shown to the signer).
            recipient_email: The signer's email address.
            recipient_name: The signer's display name.
            subject: Human subject/title for the signing request.

        Returns:
            The provider's id for the created envelope.

        Raises:
            EsignSendError: When the provider rejects or fails to accept the envelope.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_completed(
        self,
        *,
        provider_envelope_id: str,
        source_document: bytes,
        recipient_email: str,
        recipient_name: str,
        completed_at: datetime,
    ) -> CompletedArtifacts:
        """Retrieve the executed document and completion certificate for a completed envelope.

        Args:
            provider_envelope_id: The provider's envelope id (from :meth:`send`).
            source_document: The originally sent document bytes (used by the local provider to
                produce the executed copy; a real provider downloads it from its API).
            recipient_email: The signer's email, for the certificate.
            recipient_name: The signer's name, for the certificate.
            completed_at: When the signing completed, for the certificate.

        Returns:
            The signed document and its certificate of completion.

        Raises:
            EsignSendError: When the provider cannot return the artefacts.
        """
        raise NotImplementedError


def _render_certificate_pdf(
    lines: list[str], *, reference: str, completed_at: datetime
) -> bytes:
    """Lay out ``lines`` as a branded certificate-of-completion PDF and return its bytes.

    Rendered through the shared document theme (Issue #98), so the certificate carries the same
    BK ClinicQ header, footer and page numbers as every other document and an ``EXECUTED``
    stamp marking the electronic execution it records. The theme HTML-escapes each line, so a
    literal ``&`` or ``<`` (e.g. in a signer's name) is never read as markup.
    """
    meta = DocumentMeta(
        title="Certificate of Completion",
        reference=reference,
        document_date=completed_at.date(),
        stamp=StampKind.EXECUTED,
    )
    return render_lines_pdf(lines, meta=meta)


class LocalEsignProvider(EsignProvider):
    """Default provider: simulate the whole signing flow in-process, no external account needed.

    Lets the end-to-end flow run in development and CI exactly as the logging SMS provider and the
    EICAR scanner do for their channels. On completion it returns the source document as the
    executed copy and a real, generated certificate-of-completion PDF.
    """

    kind = EsignProviderKind.LOCAL

    def send(
        self,
        *,
        document: bytes,
        filename: str,
        recipient_email: str,
        recipient_name: str,
        subject: str,
    ) -> str:
        """Create a synthetic envelope, log it (no secrets) and return its id."""
        envelope_id = f"local-{uuid4()}"
        logger.info(
            "E-signature envelope created (local provider) [%s] for %s: %s",
            envelope_id,
            recipient_email,
            subject or filename,
        )
        return envelope_id

    def fetch_completed(
        self,
        *,
        provider_envelope_id: str,
        source_document: bytes,
        recipient_email: str,
        recipient_name: str,
        completed_at: datetime,
    ) -> CompletedArtifacts:
        """Return the source bytes as the signed document and a generated certificate PDF."""
        certificate = _render_certificate_pdf(
            [
                "CERTIFICATE OF COMPLETION",
                "",
                f"Envelope: {provider_envelope_id}",
                f"Signer: {recipient_name} <{recipient_email}>",
                f"Completed: {completed_at.isoformat()}",
                "",
                "This certificate records the electronic execution of the attached document.",
            ],
            reference=provider_envelope_id,
            completed_at=completed_at,
        )
        return CompletedArtifacts(
            signed_document=source_document, certificate=certificate
        )


@dataclass
class SentEnvelope:
    """One envelope captured by :class:`FakeEsignProvider`, for test assertions."""

    provider_envelope_id: str
    recipient_email: str
    recipient_name: str
    subject: str
    filename: str


@dataclass
class FakeEsignProvider(EsignProvider):
    """In-memory e-signature provider for tests: records every send, or fails on demand.

    Set ``fail_times`` to make the next N sends raise :class:`EsignSendError` (to exercise the
    graceful-degradation / paper-signature path); each failure decrements it. Accepted sends are
    kept in :attr:`sent` for assertions, and :attr:`completed_artifacts` overrides what
    :meth:`fetch_completed` returns.
    """

    kind: EsignProviderKind = EsignProviderKind.FAKE
    sent: list[SentEnvelope] = field(default_factory=list)
    fail_times: int = 0
    completed_artifacts: CompletedArtifacts | None = None

    def send(
        self,
        *,
        document: bytes,
        filename: str,
        recipient_email: str,
        recipient_name: str,
        subject: str,
    ) -> str:
        """Record the envelope and return a fake id, or raise while ``fail_times`` remains."""
        if self.fail_times > 0:
            self.fail_times -= 1
            raise EsignSendError(
                f"FakeEsignProvider forced failure to {recipient_email}"
            )
        envelope_id = f"fake-{uuid4()}"
        self.sent.append(
            SentEnvelope(
                provider_envelope_id=envelope_id,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                subject=subject,
                filename=filename,
            )
        )
        return envelope_id

    def fetch_completed(
        self,
        *,
        provider_envelope_id: str,
        source_document: bytes,
        recipient_email: str,
        recipient_name: str,
        completed_at: datetime,
    ) -> CompletedArtifacts:
        """Return the configured artefacts, or deterministic defaults for assertions."""
        if self.completed_artifacts is not None:
            return self.completed_artifacts
        return CompletedArtifacts(
            signed_document=source_document,
            certificate=f"certificate:{provider_envelope_id}".encode(),
        )


def build_esign_provider(settings: Settings | None = None) -> EsignProvider:
    """Return the e-signature provider selected by ``ESIGN_PROVIDER`` settings.

    ``FAKE`` yields a fresh in-memory double (tests usually construct and inject their own instead
    of relying on this); everything else falls back to the self-contained local provider.
    """
    settings = settings or get_settings()
    if settings.esign_provider is EsignProviderKind.FAKE:
        return FakeEsignProvider()
    return LocalEsignProvider()


# --------------------------------------------------------------------------------------
# Webhook signature — HMAC-SHA256 over the raw request body, keyed on the shared secret.
# --------------------------------------------------------------------------------------


def sign_payload(payload: bytes, secret: str) -> str:
    """Return the lowercase-hex HMAC-SHA256 of ``payload`` under ``secret``.

    The single definition of a webhook signature, used both to verify an incoming callback and (by
    the local provider / tests) to sign a simulated one, so the two can never disagree on scheme.
    """
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """Return whether ``signature`` is a valid HMAC-SHA256 of ``payload`` under ``secret``.

    Constant-time compared. Returns False (rather than raising) when the secret is unset or the
    signature is missing/malformed, so a caller reveals nothing about *why* a webhook was rejected.
    """
    if not secret or not signature:
        return False
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature)
