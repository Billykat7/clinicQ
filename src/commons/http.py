"""Shared HTTP helpers for serving stored file bytes (Issue #99).

Signed-download routes serve the *same* bytes for both a save-to-disk **download** and an in-app
**view** (inline preview). The only difference is the ``Content-Disposition`` header, chosen by the
caller via a ``disposition`` query parameter; both paths are otherwise identical — same signed-link
security, same audit log. This module centralises the header so every serve route builds it the same
way and safely.
"""

from __future__ import annotations

from typing import Literal

#: How the browser should present served bytes: ``attachment`` saves the file (the default),
#: ``inline`` renders it in place (the native PDF/image viewer) for the in-app document viewer.
DownloadDisposition = Literal["attachment", "inline"]


def content_disposition_header(disposition: DownloadDisposition, filename: str) -> str:
    """Build a ``Content-Disposition`` value for ``filename`` with the chosen disposition.

    The filename is sanitised for the quoted ``filename`` form — double quotes, control characters
    and header-splitting bytes (CR/LF) are stripped — so a stored filename can never break out of the
    header. ``disposition`` is a typed literal (``attachment``/``inline``), so it is safe verbatim.

    Args:
        disposition: ``"attachment"`` to download, ``"inline"`` to preview in the browser.
        filename: The name shown to the user / used when saving.

    Returns:
        A header value such as ``inline; filename="lease.pdf"``.
    """
    safe = "".join(c for c in filename if c.isprintable() and c not in '"\\').strip()
    if not safe:
        safe = "document"
    return f'{disposition}; filename="{safe}"'
