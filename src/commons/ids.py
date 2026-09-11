"""Identifier strategy: UUIDv7 strings, which sort by creation time and are safe in a URL.

Every table keys on a ``String(36)`` id defaulted in Python (see ``src/database/models/widget.py``),
so a row has its id before the flush and can be referenced in the same transaction, for example by
an audit event. The kernel filled those columns with random UUIDv4s; new models use
:func:`new_id` instead, which keeps the column type and the canonical 36-character form and adds
two properties:

* **Sortable by creation time.** A UUIDv7 (RFC 9562) starts with a 48-bit Unix timestamp in
  milliseconds, and CPython's :func:`uuid.uuid7` fills the next 42 bits with a counter, so ids made
  in the same millisecond still come out in order. Ordering by ``id`` is ordering by creation, and
  new rows land at the right-hand edge of the primary-key index instead of at random pages.
* **Safe in a URL.** The canonical form is lowercase hex and hyphens, all RFC 3986 unreserved
  characters, so an id goes into a path or query string without escaping.

The timestamp is visible to anyone who holds the id. That is fine for the records ClinicQ keys this
way (a ticket's join time is on the ticket anyway); do not use one as a secret or an unguessable
token: those come from :mod:`secrets`.

PostgreSQL 18 can generate the same format with ``uuidv7()`` if a table ever needs a server-side
default.
"""

import uuid
from datetime import datetime

from src.commons.time import APP_TIMEZONE

#: The UUID version :func:`new_id` produces.
ID_UUID_VERSION = 7


def new_id() -> str:
    """Return a new identifier: a UUIDv7 in canonical form (36 characters, lowercase hex).

    Use it as a model's primary-key default: ``mapped_column(String(36), primary_key=True,
    default=new_id)``.
    """
    return str(uuid.uuid7())


def id_created_at(identifier: str) -> datetime:
    """Return when a :func:`new_id` identifier was minted, as an aware SAST datetime.

    Millisecond precision: the resolution of the UUIDv7 timestamp. Useful when reading logs or
    debugging ordering; never a substitute for the row's own ``created_at``.

    Args:
        identifier: A UUIDv7 string.

    Returns:
        The embedded creation instant in ``Africa/Johannesburg``.

    Raises:
        ValueError: If ``identifier`` is not a UUID, or is a UUID of another version (a kernel
            UUIDv4 carries no timestamp).
    """
    parsed = uuid.UUID(identifier)
    if parsed.version != ID_UUID_VERSION:
        raise ValueError(
            f"{identifier} is a version {parsed.version} UUID; only version "
            f"{ID_UUID_VERSION} ids carry a creation time."
        )
    # For version 7, ``UUID.time`` is the 48-bit Unix timestamp in milliseconds (Python 3.14).
    return datetime.fromtimestamp(parsed.time / 1000, tz=APP_TIMEZONE)
