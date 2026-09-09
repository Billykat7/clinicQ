"""Profile-picture validation, processing and private storage (Issue #130, M23).

Everything that turns an uploaded file into a stored avatar lives here, so the router and the
``/me`` surface stay thin. Three concerns, in the order an upload meets them:

* :func:`sniff_image_type` — decide what the bytes *actually* are from their magic bytes, not
  from the ``Content-Type`` the client claimed. A declared type is a hint from an untrusted
  source; the signature is evidence. Both must agree, so a script renamed ``avatar.png`` is
  refused before it is ever decoded.
* :func:`process_avatar` — decode with Pillow, apply the EXIF orientation so a phone photo is the
  right way up, centre-crop to a square, downscale to the configured side length, and re-encode
  to WEBP. The output is bytes **we** produced: no EXIF (so no GPS coordinates of the user's
  home), no ICC profile, no trailing data, and one stored format for every upload.
* :class:`LocalAvatarStorage` — write, read and delete those bytes under an opaque token, with
  the same discipline as the document and photo stores, so swapping in S3 later is a drop-in.

The token (:func:`new_avatar_token`) is the single identifier for a stored avatar: it is what the
user row holds, what the serving URL exposes, and what the storage maps to a path. It is random —
never derived from the user — so a URL discloses nothing about whose picture it is and cannot be
guessed from anyone else's. It is **new on every upload**, which is what makes a stable,
session-free serving URL safe: replacing or removing a picture retires its URL immediately,
because nothing points at the old token any more (and a new picture is a new URL, so a cache can
never serve a stale one).
"""

import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from src.commons.enums import ImageContentType
from src.commons.exceptions import InvalidImageError

# The stored format for every avatar, whatever was uploaded. One format keeps the serving path
# trivial (a single ``Content-Type``) and the re-encode unconditional; WEBP is the smallest of the
# three accepted types at equivalent quality and is supported by every browser this app targets.
AVATAR_CONTENT_TYPE = "image/webp"
_AVATAR_PILLOW_FORMAT = "WEBP"
_AVATAR_QUALITY = 82
_AVATAR_FILE_EXTENSION = "webp"

# A token is exactly a UUID4's hex form. Validated on the way in from a URL, which makes path
# traversal impossible **by construction** rather than by a separate guard: a string that is not
# 32 hex characters never becomes a path at all.
_TOKEN_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")

# Magic-byte signatures for the image types an avatar may be uploaded as. A JPEG starts SOI, a
# PNG has its fixed 8-byte header, and a WEBP is a RIFF container whose form type is ``WEBP`` at
# offset 8 — hence the two-part check for that one.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_RIFF_MAGIC = b"RIFF"
_WEBP_FORM = b"WEBP"

# Enough bytes to cover the longest signature check (RIFF's form type ends at offset 12).
_SNIFF_BYTES = 12


def sniff_image_type(data: bytes) -> ImageContentType | None:
    """Return the image type ``data`` actually is, or ``None`` when it is not a known image.

    Reads only the leading magic bytes. This is a *gate*, not a guarantee of a well-formed image —
    :func:`process_avatar` is what proves the file decodes — but it means bytes that were never an
    image are rejected before any decoder touches them.
    """
    header = data[:_SNIFF_BYTES]
    if header.startswith(_JPEG_MAGIC):
        return ImageContentType.JPEG
    if header.startswith(_PNG_MAGIC):
        return ImageContentType.PNG
    if header.startswith(_RIFF_MAGIC) and header[8:12] == _WEBP_FORM:
        return ImageContentType.WEBP
    return None


def new_avatar_token() -> str:
    """Return a fresh, unguessable token identifying one stored avatar."""
    return uuid4().hex


def is_valid_avatar_token(token: str) -> bool:
    """Return whether ``token`` has the exact shape this module issues."""
    return bool(_TOKEN_PATTERN.match(token))


def process_avatar(data: bytes, *, size_px: int) -> bytes:
    """Turn an accepted upload into the square WEBP avatar that gets stored.

    The pipeline, and why each step is there:

    1. **Decode** with Pillow — bytes that cannot be opened are not an image, whatever their
       magic bytes said.
    2. **Apply the EXIF orientation** (``ImageOps.exif_transpose``) so a photo taken in portrait
       on a phone is stored the way it was seen, then drop the metadata entirely.
    3. **Centre-crop to a square** — every avatar renders in a circle or square, so cropping here
       (rather than in CSS) means the stored file is exactly what is displayed.
    4. **Downscale** to ``size_px`` on a side, never *up*: enlarging a small picture only spends
       bytes on invented pixels, so a 64px upload stays 64px.
    5. **Re-encode to WEBP**, so the stored bytes are freshly produced by us — no EXIF (and so no
       GPS coordinates of the user's home), no ICC profile, no appended payload.

    Args:
        data: The uploaded bytes (already sniffed as an accepted image type and within the cap).
        size_px: Maximum side length of the stored square avatar.

    Returns:
        The WEBP bytes to store.

    Raises:
        InvalidImageError: The bytes could not be decoded or re-encoded as a real image.
    """
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()  # force a full decode now, so a truncated file fails here
            # ``exif_transpose`` returns a new image with the rotation baked into the pixels and
            # the orientation tag gone; the fallback keeps a metadata-less image working.
            oriented = ImageOps.exif_transpose(image) or image
            # The square can never be larger than the shortest side, so a small upload is
            # cropped but not enlarged.
            side = min(size_px, min(oriented.size))
            # ``fit`` is a cover-style crop: it fills the square from the centre of the subject.
            square = ImageOps.fit(
                oriented.convert("RGB"),
                (side, side),
                method=Image.Resampling.LANCZOS,
            )
            buffer = BytesIO()
            square.save(
                buffer, format=_AVATAR_PILLOW_FORMAT, quality=_AVATAR_QUALITY, method=4
            )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("Uploaded file is not a valid image.") from exc
    return buffer.getvalue()


class LocalAvatarStorage:
    """Store, read and remove avatar objects, addressed by their opaque token.

    The same deliberately small surface as
    :class:`~src.modules.tenants.storage.LocalDocumentStorage` — ``save`` / ``read`` / ``delete``
    — so swapping in S3 later is a drop-in. Unlike that store there is no path-traversal *guard*,
    because there is no path to guard: a token is validated against
    :func:`is_valid_avatar_token` before it is ever turned into a filename, so nothing but 32 hex
    characters can reach the filesystem.

    Objects are sharded one level deep by the token's first two characters, so a large deployment
    does not end up with every avatar in a single directory.
    """

    def __init__(self, base_dir: str | Path) -> None:
        """Bind the storage to ``base_dir`` (created lazily on first write)."""
        self._base = Path(base_dir)

    def _path_for(self, token: str) -> Path:
        """Return the file path for ``token``.

        Raises:
            ValueError: ``token`` is not a token this module issued.
        """
        if not is_valid_avatar_token(token):
            raise ValueError(f"Not a valid avatar token: {token!r}")
        return self._base / token[:2] / f"{token}.{_AVATAR_FILE_EXTENSION}"

    def save(self, token: str, data: bytes) -> None:
        """Write ``data`` for ``token`` (creating parent directories as needed)."""
        path = self._path_for(token)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, token: str) -> bytes:
        """Return the bytes stored for ``token``.

        Raises:
            FileNotFoundError: Nothing is stored for ``token`` (e.g. it was replaced).
            ValueError: ``token`` is not a token this module issued.
        """
        return self._path_for(token).read_bytes()

    def delete(self, token: str) -> None:
        """Remove the object for ``token`` if present (a missing object is not an error)."""
        self._path_for(token).unlink(missing_ok=True)
