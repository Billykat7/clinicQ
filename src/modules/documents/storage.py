"""Private local object storage for the document service, with integrity checksums (Issue #70).

Where the bytes of every consolidated document — a tenant's ID copy, a signed lease, a maintenance
photo — physically live. A deliberately small surface (``save`` / ``read`` / ``delete`` under an
opaque key) mirroring the per-module stores it replaces (e.g.
``src.modules.tenants.storage.LocalDocumentStorage``) so swapping in S3 later is a drop-in.

The one addition over those stores is **integrity**: :func:`compute_checksum` is the SHA-256 the
ingest records and the download re-computes, so a corrupted or tampered object fails the download
loudly rather than being served. Bytes are stored verbatim (a signed lease PDF must survive
byte-for-byte), so the content-type allow-list and size cap are enforced upstream in the router
before the body is buffered.

Keys are resolved with the same path-traversal guard as the other stores, so a crafted key can never
escape the base directory.
"""

import hashlib
from pathlib import Path


def compute_checksum(data: bytes) -> str:
    """Return the lowercase-hex SHA-256 digest of ``data``.

    The single definition of a document's checksum, used both when recording it at ingest and when
    verifying it on download, so the two can never disagree on the algorithm.
    """
    return hashlib.sha256(data).hexdigest()


class LocalObjectStorage:
    """Store, read and remove document objects under a base directory on local disk."""

    def __init__(self, base_dir: str | Path) -> None:
        """Bind the storage to ``base_dir`` (created lazily on first write)."""
        self._base = Path(base_dir)

    def _resolve(self, key: str) -> Path:
        """Return the absolute path for ``key``, guarding against path traversal."""
        target = (self._base / key).resolve()
        base = self._base.resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"Storage key escapes the base directory: {key!r}")
        return target

    def save(self, key: str, data: bytes) -> None:
        """Write ``data`` to ``key`` (creating parent directories as needed)."""
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key: str) -> bytes:
        """Return the bytes stored under ``key``.

        Raises:
            FileNotFoundError: No object is stored under ``key`` (e.g. it was deleted).
        """
        return self._resolve(key).read_bytes()

    def delete(self, key: str) -> None:
        """Remove the object at ``key`` if present (a missing object is not an error)."""
        self._resolve(key).unlink(missing_ok=True)
