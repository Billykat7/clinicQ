"""Unit tests for the shared ``Content-Disposition`` helper (Issue #99)."""

from __future__ import annotations

import pytest

from src.commons.http import content_disposition_header


def test_builds_attachment_and_inline_for_a_plain_filename() -> None:
    """The disposition is emitted verbatim with a quoted filename."""
    assert (
        content_disposition_header("attachment", "lease.pdf")
        == 'attachment; filename="lease.pdf"'
    )
    assert (
        content_disposition_header("inline", "lease.pdf")
        == 'inline; filename="lease.pdf"'
    )


@pytest.mark.parametrize(
    ("raw", "expected_name"),
    [
        ('quote".pdf', "quote.pdf"),  # a double quote can't break out of the header
        ("back\\slash.pdf", "backslash.pdf"),  # nor a backslash
        ("line\r\nbreak.pdf", "linebreak.pdf"),  # nor a CR/LF header-split
        ("   ", "document"),  # an all-whitespace/empty name falls back
        ("", "document"),
    ],
)
def test_sanitises_the_filename(raw: str, expected_name: str) -> None:
    """Control characters, quotes and header-splitting bytes are stripped; empty falls back."""
    assert (
        content_disposition_header("inline", raw)
        == f'inline; filename="{expected_name}"'
    )
