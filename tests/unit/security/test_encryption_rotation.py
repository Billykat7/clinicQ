"""Field-encryption key rotation: multi-key decrypt and forward re-encryption (Issue #78).

Essential, isolated logic worth unit-testing on its own: that a cipher built with a new key in
front of an old key encrypts under the new key while still reading tokens written under the old
one (the zero-downtime rotation guarantee), and that :meth:`FieldCipher.rotate` migrates a token
forward onto the primary key so the retired key can eventually be dropped.
"""

import pytest

from src.core.encryption import (
    FieldDecryptionError,
    build_field_cipher,
)

_OLD = "old-field-encryption-secret-old-old-old"
_NEW = "new-field-encryption-secret-new-new-new"


def test_new_key_decrypts_old_ciphertext() -> None:
    """A cipher with [new, old] reads a token written under the old key (no downtime on rotate)."""
    old_only = build_field_cipher(_OLD)
    token = old_only.encrypt("9001015800086")

    rotating = build_field_cipher(_NEW, _OLD)  # new is primary, old kept for decrypt
    assert rotating.decrypt(token) == "9001015800086"


def test_new_data_is_written_under_primary_key() -> None:
    """During rotation, fresh ciphertext is under the primary (new) key — the old key alone can't read it."""
    rotating = build_field_cipher(_NEW, _OLD)
    token = rotating.encrypt("secret data")

    old_only = build_field_cipher(_OLD)
    with pytest.raises(FieldDecryptionError):
        old_only.decrypt(token)

    # And the new key alone reads it, so once migration completes the old key can be dropped.
    new_only = build_field_cipher(_NEW)
    assert new_only.decrypt(token) == "secret data"


def test_rotate_migrates_token_onto_primary_key() -> None:
    """``rotate`` re-encrypts an old-key token so the new key alone can then read it."""
    old_only = build_field_cipher(_OLD)
    old_token = old_only.encrypt("migrate me")

    rotating = build_field_cipher(_NEW, _OLD)
    migrated = rotating.rotate(old_token)

    new_only = build_field_cipher(_NEW)
    assert new_only.decrypt(migrated) == "migrate me"
    # The retired key can no longer read the migrated token.
    with pytest.raises(FieldDecryptionError):
        old_only.decrypt(migrated)


def test_rotate_of_unknown_token_is_rejected() -> None:
    """A token under none of the cipher's keys cannot be rotated (fails closed)."""
    stranger = build_field_cipher("some-unrelated-secret-value-abcdefgh").encrypt("x")
    rotating = build_field_cipher(_NEW, _OLD)
    with pytest.raises(FieldDecryptionError):
        rotating.rotate(stranger)


def test_single_key_cipher_still_round_trips() -> None:
    """The common single-key path is unchanged: encrypt -> decrypt round-trips."""
    cipher = build_field_cipher(_NEW)
    assert cipher.decrypt(cipher.encrypt("plain")) == "plain"
