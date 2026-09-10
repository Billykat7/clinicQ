"""Field encryption core: cipher round-trip and ID-number masking (Issue #33).

Essential, isolated logic worth unit-testing on its own: that a value survives an
encrypt/decrypt round-trip, that a token is authenticated (tampering is rejected), that a
wrong key cannot read a token, and that :func:`mask_id_number` never reveals more than the
last few characters. The transparent :class:`EncryptedString` column is exercised end-to-end
in ``tests/unit/test_tenant_model.py``.
"""

import pytest

from src.core.encryption import (
    FieldDecryptionError,
    build_field_cipher,
    mask_id_number,
)


def test_encrypt_decrypt_round_trip() -> None:
    """A value survives an encrypt -> decrypt round-trip unchanged."""
    cipher = build_field_cipher("a-secret-that-derives-a-fernet-key")
    token = cipher.encrypt("9001015800086")

    assert token != "9001015800086"  # never stored as plaintext
    assert cipher.decrypt(token) == "9001015800086"


def test_ciphertext_is_non_deterministic() -> None:
    """Fernet embeds a random IV, so the same plaintext encrypts to different tokens."""
    cipher = build_field_cipher("another-secret-value-for-the-key")

    assert cipher.encrypt("same-value") != cipher.encrypt("same-value")


def test_decrypt_with_wrong_key_is_rejected() -> None:
    """A token cannot be read with a cipher built from a different secret."""
    token = build_field_cipher("secret-one-secret-one-secret-one").encrypt(
        "secret data"
    )
    other = build_field_cipher("secret-two-secret-two-secret-two")

    with pytest.raises(FieldDecryptionError):
        other.decrypt(token)


def test_decrypt_of_tampered_token_is_rejected() -> None:
    """Fernet authenticates every token, so a mutated token fails to decrypt."""
    cipher = build_field_cipher("secret-for-tamper-detection-check")
    token = cipher.encrypt("original")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    with pytest.raises(FieldDecryptionError):
        cipher.decrypt(tampered)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9001015800086", "*********0086"),
        ("1234", "****"),
        ("12", "**"),
        ("", ""),
        (None, None),
    ],
)
def test_mask_id_number(raw: str | None, expected: str | None) -> None:
    """Masking reveals at most the last four characters and preserves length."""
    assert mask_id_number(raw) == expected
