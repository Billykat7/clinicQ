"""Symmetric field encryption for personal data stored at rest (Issue #33).

A tenant's national ID number is personal data that must never sit in the database as
plaintext. This module provides that guarantee in two layers:

* :class:`FieldCipher` — a thin wrapper over Fernet (AES-128-CBC + HMAC-SHA256 authenticated
  encryption) that turns a string into an opaque, tamper-evident token and back;
* :class:`EncryptedString` — a SQLAlchemy :class:`~sqlalchemy.types.TypeDecorator` that
  encrypts on the way to the database and decrypts on the way out, so a mapped column is
  plaintext in Python and ciphertext on disk with no per-call code at the model boundary.

The key is derived from the application secret so encryption works out of the box in
development and tests without extra configuration: set ``FIELD_ENCRYPTION_KEY`` to pin an
explicit key, otherwise the key is derived deterministically from ``JWT_SECRET`` (already a
required strong secret outside development — see :mod:`src.core.config`). Deriving with
SHA-256 yields exactly the 32 url-safe base64 bytes Fernet expects.

**Key rotation (Issue #78).** A cipher is built over an *ordered list* of keys via
:class:`~cryptography.fernet.MultiFernet`: new data is always encrypted with the **first**
(primary) key, while decryption is attempted against **every** key in turn. That is what makes
a zero-downtime rotation possible — set ``FIELD_ENCRYPTION_KEYS`` to a comma-separated list with
the new secret first and the old secret(s) after it, deploy, and the app immediately writes
under the new key while still reading everything written under the old one. A background
re-encryption pass (:func:`rotate_token`, driven by ``python -m src.core.encryption``) then
migrates existing ciphertext onto the primary key, after which the retired secret can be dropped
from the list. The full rehearsed procedure lives in ``docs/CICD/KEY-ROTATION.md``.

Masking (for list responses that must not echo even the ciphertext back) lives in
:func:`mask_id_number`.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from src.core.config import Settings, get_settings

# How many trailing characters :func:`mask_id_number` leaves visible; everything before is
# replaced with a fixed mask character so a list response reveals only enough to disambiguate.
_VISIBLE_TAIL = 4
_MASK_CHAR = "*"


class FieldDecryptionError(Exception):
    """Raised when a stored value cannot be decrypted (wrong key or tampered ciphertext)."""


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a valid Fernet key (32 url-safe base64 bytes) from an arbitrary secret.

    Fernet requires a key that is exactly 32 bytes, url-safe base64 encoded. Hashing the
    secret with SHA-256 always yields 32 raw bytes regardless of the secret's length, which
    base64 then encodes to the 44-character key Fernet accepts.
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class FieldCipher:
    """Encrypt and decrypt short strings with authenticated symmetric encryption.

    Wraps a :class:`~cryptography.fernet.MultiFernet` over one or more keys, **primary first**.
    New tokens are always produced with the primary (first) key; decryption is attempted against
    every key in order, so a cipher built with ``[new, old]`` writes under ``new`` while still
    reading tokens written under ``old`` — the mechanism behind zero-downtime key rotation. A
    single-key cipher (the common case) behaves exactly like a plain Fernet.

    Instances are cheap but immutable; build one via :func:`build_field_cipher` (which caches by
    key material).
    """

    def __init__(self, keys: tuple[bytes, ...]) -> None:
        """Initialise the cipher from url-safe base64 Fernet keys, primary (encrypt) key first.

        Raises:
            ValueError: No keys were supplied (a cipher must have at least one key).
        """
        if not keys:
            raise ValueError("FieldCipher requires at least one key.")
        self._multi = MultiFernet([Fernet(key) for key in keys])

    def encrypt(self, plaintext: str) -> str:
        """Return an opaque Fernet token for ``plaintext`` using the primary key (str in/out)."""
        return self._multi.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Return the plaintext for a Fernet ``token``, trying every key in order.

        Raises:
            FieldDecryptionError: The token matches none of the cipher's keys or has been
                tampered with (Fernet authenticates every token).
        """
        try:
            return self._multi.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise FieldDecryptionError(
                "Stored value could not be decrypted (wrong key or corrupted data)."
            ) from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt an existing ``token`` onto the primary key without changing its plaintext.

        Used by the offline re-encryption pass so ciphertext written under a retired key is
        migrated forward, after which the retired key can be dropped. A no-op in effect when the
        token is already under the primary key (it is simply re-wrapped with a fresh IV).

        Raises:
            FieldDecryptionError: The token matches none of the cipher's keys or is corrupt.
        """
        try:
            return self._multi.rotate(token.encode("ascii")).decode("ascii")
        except InvalidToken as exc:
            raise FieldDecryptionError(
                "Stored value could not be rotated (wrong key or corrupted data)."
            ) from exc


@lru_cache(maxsize=8)
def build_field_cipher(*secrets: str) -> FieldCipher:
    """Build (and memoise) a :class:`FieldCipher` from one or more secrets, primary first.

    Each secret is turned into a Fernet key via :func:`_derive_fernet_key`. Cached by the tuple
    of secrets so key derivation runs once per distinct key set rather than on every encrypt or
    decrypt at the ORM boundary. Called with a single secret it yields a plain single-key cipher
    (the pre-rotation behaviour and signature callers already rely on).
    """
    keys = tuple(_derive_fernet_key(secret) for secret in secrets)
    return FieldCipher(keys)


def _configured_secrets(settings: Settings) -> tuple[str, ...]:
    """Resolve the ordered encryption secrets from settings, primary (newest) first.

    ``FIELD_ENCRYPTION_KEYS`` (comma-separated, newest first) fully specifies the key list and
    is what a rotation edits. When it is unset the cipher falls back to a single key —
    ``FIELD_ENCRYPTION_KEY`` if pinned, otherwise ``JWT_SECRET`` — so development and tests need
    no extra configuration.
    """
    listed = [s.strip() for s in settings.field_encryption_keys.split(",") if s.strip()]
    if listed:
        return tuple(listed)
    return (settings.field_encryption_key or settings.jwt_secret,)


def get_field_cipher() -> FieldCipher:
    """Return the process-wide field cipher over the configured (possibly rotating) key list.

    Encrypts with the primary key and decrypts against every configured key, so a mid-rotation
    deployment reads old ciphertext while writing new — see :func:`_configured_secrets`.
    """
    return build_field_cipher(*_configured_secrets(get_settings()))


def rotate_token(token: str) -> str:
    """Re-encrypt a stored token onto the current primary key (offline re-encryption pass).

    Thin module-level wrapper over :meth:`FieldCipher.rotate` on the process cipher, so the
    ``python -m src.core.encryption`` migration and any batch job share one entry point.
    """
    return get_field_cipher().rotate(token)


class EncryptedString(TypeDecorator[str]):
    """A ``String`` column whose value is encrypted at rest and decrypted transparently.

    The Python-side value is always plaintext; the database only ever sees a Fernet token
    (stored as ``TEXT`` because a token is meaningfully longer than its plaintext). The
    cipher is resolved lazily from settings at bind/result time — never captured at import —
    so key configuration is honoured wherever the type is used.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        """Use ``TEXT`` for the ciphertext regardless of dialect."""
        return dialect.type_descriptor(String())

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Encrypt the plaintext on the way into the database (``None`` passes through)."""
        if value is None:
            return None
        return get_field_cipher().encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Decrypt the stored token on the way out of the database (``None`` passes through)."""
        if value is None:
            return None
        return get_field_cipher().decrypt(value)


def mask_id_number(id_number: str | None) -> str | None:
    """Return a masked ID number that reveals only its last few characters.

    Used by list projections so a directory response never echoes a full ID number. All but
    the final :data:`_VISIBLE_TAIL` characters are replaced with ``*``; a value at or below
    that length is fully masked (length preserved) so nothing identifying leaks. ``None``
    passes through unchanged.
    """
    if id_number is None:
        return None
    if len(id_number) <= _VISIBLE_TAIL:
        return _MASK_CHAR * len(id_number)
    masked_len = len(id_number) - _VISIBLE_TAIL
    return _MASK_CHAR * masked_len + id_number[-_VISIBLE_TAIL:]


# Every ``(table, column)`` stored via :class:`EncryptedString`. The re-encryption pass walks
# this registry, so adding a new encrypted column means adding it here — the one place the
# rotation job needs to know about. Schema-qualification is resolved at run time from the model
# metadata, so this stays a plain, dialect-agnostic list.
ENCRYPTED_COLUMNS: tuple[tuple[str, str], ...] = (("tenant", "id_number"),)


def reencrypt_all(session: Any) -> int:
    """Re-encrypt every stored :class:`EncryptedString` value onto the current primary key.

    The offline half of a key rotation: after ``FIELD_ENCRYPTION_KEYS`` is deployed with the new
    key first, this walks :data:`ENCRYPTED_COLUMNS` and rewrites each non-null ciphertext via
    :func:`rotate_token`, working at the raw-column level (``text()`` SQL) so it moves ciphertext
    directly rather than round-tripping plaintext through the ORM. Once every value is under the
    primary key the retired key can be dropped from the list.

    Args:
        session: An open SQLAlchemy session bound to the target database.

    Returns:
        The number of values re-encrypted across all columns.
    """
    from sqlalchemy import text

    from src.commons.enums import DbSchema
    from src.database.models.base import Base

    schema = DbSchema.CLINICQ.value
    dialect = session.get_bind().dialect.name
    qualified = (lambda t: t) if dialect == "sqlite" else (lambda t: f"{schema}.{t}")

    rotated = 0
    for table, column in ENCRYPTED_COLUMNS:
        _ = Base  # ensure models/metadata are importable in this process
        name = qualified(table)
        rows = session.execute(
            text(f"SELECT id, {column} FROM {name} WHERE {column} IS NOT NULL")
        ).all()
        for row_id, ciphertext in rows:
            session.execute(
                text(f"UPDATE {name} SET {column} = :new WHERE id = :id"),
                {"new": rotate_token(ciphertext), "id": row_id},
            )
            rotated += 1
    session.commit()
    return rotated


def _main() -> None:
    """Run the offline re-encryption pass against the configured database (CLI entrypoint)."""
    import logging

    from src.database.session import get_db_context

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with get_db_context() as session:
        count = reencrypt_all(session)
    logging.getLogger(__name__).info(
        "Re-encrypted %d value(s) onto the current primary field-encryption key.", count
    )


if __name__ == "__main__":
    _main()
