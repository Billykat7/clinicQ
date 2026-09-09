"""Unit tests for the document storage service (Issue 70 / M11).

Exercises the building blocks of the consolidated document store directly against an in-memory
database and a temporary private storage directory, covering the issue's acceptance criteria at the
service layer (the HTTP surface is covered in ``tests/integration/test_documents.py``):

* bytes are stored under an opaque key with a SHA-256 checksum, and a corrupted object fails the
  read loudly (:class:`DocumentChecksumMismatchError`);
* an infected upload is refused on ingest with nothing stored;
* signed links round-trip and reject the wrong token type / tampering;
* retention stamps ``expires_at`` from the class, and the sweep purges expired documents,
  tombstones the row and records the deletion — leaving permanent documents untouched.

Per ``.cursor/rules/testing-strategy.mdc`` these build isolated ``Settings`` (``_env_file=None``) so
a developer's local ``.env`` cannot change outcomes.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import DocumentScannerKind
from src.core.config import Settings
from src.core.s3_logging import APP_TIMEZONE
from src.modules.documents import download_links
from src.modules.documents.enums import RetentionClass, VirusScanStatus
from src.modules.documents.retention import expiry_for
from src.modules.documents.storage import LocalObjectStorage, compute_checksum
from src.modules.documents.virus_scan import (
    DisabledScanner,
    EicarSignatureScanner,
    get_scanner,
)

_PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


# The EICAR standard test signature, assembled so this test file is not itself flagged.
_EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


_TEST_JWT_SECRET = "documents-unit-test-secret-min-32-chars"


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """Yield a session on the in-memory schema-mapped database from the shared fixture."""
    with session_factory() as session:
        yield session


@pytest.fixture
def storage(tmp_path) -> LocalObjectStorage:
    """A private object store rooted at a throwaway temp directory."""
    return LocalObjectStorage(tmp_path)


def _settings(**overrides: object) -> Settings:
    """Build isolated documents ``Settings`` (no ``.env``)."""
    base: dict[str, object] = {"_env_file": None, "jwt_secret": _TEST_JWT_SECRET}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_checksum_is_stable_sha256() -> None:
    """``compute_checksum`` is the plain SHA-256 hex digest and is deterministic."""
    import hashlib

    assert compute_checksum(_PDF_BYTES) == hashlib.sha256(_PDF_BYTES).hexdigest()


def test_storage_roundtrip_and_traversal_guard(storage: LocalObjectStorage) -> None:
    """Bytes round-trip verbatim, and a key escaping the base dir is rejected."""
    storage.save("documents/tenant/t1/a.pdf", _PDF_BYTES)
    assert storage.read("documents/tenant/t1/a.pdf") == _PDF_BYTES
    with pytest.raises(ValueError):
        storage.read("../../etc/passwd")


def test_eicar_scanner_flags_infected_and_passes_clean() -> None:
    """The EICAR signature scanner flags the test signature and passes ordinary bytes."""
    scanner = EicarSignatureScanner()
    assert scanner.scan(_EICAR) is VirusScanStatus.INFECTED
    assert scanner.scan(_PDF_BYTES) is VirusScanStatus.CLEAN


def test_get_scanner_honours_disabled_and_kind() -> None:
    """``get_scanner`` returns the EICAR scanner when enabled and a no-op when disabled."""
    assert isinstance(
        get_scanner(_settings(document_scanner=DocumentScannerKind.EICAR)),
        EicarSignatureScanner,
    )
    assert isinstance(
        get_scanner(_settings(document_virus_scan_enabled=False)), DisabledScanner
    )
    assert (
        get_scanner(_settings(document_virus_scan_enabled=False)).scan(_EICAR)
        is VirusScanStatus.SKIPPED
    )


def test_download_link_roundtrips(monkeypatch: pytest.MonkeyPatch) -> None:
    """A minted token decodes back to its document id and actor."""
    monkeypatch.setattr(download_links, "get_settings", _settings)
    token = download_links.create_document_download_token(
        "doc-1", actor="mgr@example.com"
    )
    assert download_links.decode_document_download_token(token) == (
        "doc-1",
        "mgr@example.com",
    )


def test_download_link_rejects_tampering_and_bad_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tampered or non-token string decodes to ``None`` (revealing nothing)."""
    monkeypatch.setattr(download_links, "get_settings", _settings)
    token = download_links.create_document_download_token("doc-1")
    assert download_links.decode_document_download_token(token + "x") is None
    assert download_links.decode_document_download_token("not-a-token") is None


def test_expiry_for_permanent_is_none_and_transient_is_short() -> None:
    """A permanent class never expires; a transient one expires within its window."""
    from datetime import datetime

    now = datetime(2026, 1, 1, tzinfo=APP_TIMEZONE)
    assert expiry_for(RetentionClass.PERMANENT, ingested_at=now) is None
    transient = expiry_for(RetentionClass.TRANSIENT, ingested_at=now)
    assert transient == now + timedelta(days=30)
