"""Email addresses: the one place a patient's address is normalised and masked (Issue 219).

The sibling of :mod:`src.commons.phone`, and it exists for the same reason. A patient who signs in
with an address *is* that address, so two spellings of one address must never become two patients:
``Nomsa@Gmail.COM`` and ``nomsa@gmail.com`` are one person, and the unique constraint on
``patient.email`` can only hold if every lookup and insert goes through :func:`normalize_email`.

Rules, deliberately narrow:

* **Lower-cased and trimmed.** The domain is case-insensitive by RFC 1035, and the local part is
  case-*sensitive* in the standard — but no mail provider a patient uses treats it that way, and
  one patient with two records because they capitalised their own name is a worse outcome than the
  theoretical address we refuse to tell apart. Staff accounts already lower-case (Issue 15).
* **One ``@``, something either side, a dot in the domain.** Not a validator that tries to decide
  what RFC 5321 permits: the code we send is the real test, exactly as delivery is the real test
  for a phone number. What this refuses is what a person can see is wrong — no ``@``, no domain,
  spaces inside — so the answer comes back before an email is attempted.
* **Not a staff address check.** A patient's address and a staff account's address are unrelated
  namespaces: the same person may hold both, and one never authenticates the other (a patient
  token opens no staff route, :mod:`src.modules.patients.sessions`).

Nothing else in the codebase parses a patient's address.
"""

import re
from typing import Final

from src.commons.exceptions import UnprocessableError

#: The longest address we store, and the width of ``patient.email``. RFC 5321's limit.
MAX_EMAIL_LENGTH: Final = 254
#: Something, one ``@``, something, a dot, something — with no whitespace anywhere.
_SHAPE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: What a patient is told for an address that cannot be read, however it is wrong. It never repeats
#: what they typed: it is personal information, and error envelopes are logged.
INVALID_EMAIL_MESSAGE: Final = "Enter an email address such as name@example.com."


class InvalidEmailAddressError(UnprocessableError):
    """An address that cannot be normalised: HTTP 422, code ``patients.email.invalid``."""

    def __init__(self) -> None:
        """One message for every way an address can be wrong."""
        super().__init__(INVALID_EMAIL_MESSAGE, code="patients.email.invalid")


def normalize_email(raw: str) -> str:
    """Return ``raw`` lower-cased and trimmed, or raise :class:`InvalidEmailAddressError`.

    Idempotent: a normalised address normalises to itself.
    """
    text = raw.strip().lower()
    if len(text) > MAX_EMAIL_LENGTH or not _SHAPE.match(text):
        raise InvalidEmailAddressError()
    return text


def mask_email(address: str) -> str:
    """``nomsa@gmail.com`` → ``n••••a@gmail.com``: enough to recognise your own, not to read it.

    The domain is left alone — it is not what identifies a person — and the local part keeps its
    first and last character. A local part of one or two characters is masked whole rather than
    given away by the rule.
    """
    local, _, domain = address.partition("@")
    if len(local) <= 2:
        return f"{'•' * len(local)}@{domain}"
    return f"{local[0]}{'•' * (len(local) - 2)}{local[-1]}@{domain}"
