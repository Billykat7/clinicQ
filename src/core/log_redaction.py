"""Redaction in the logging layer (Issue 6): phone numbers, OTPs and tokens never reach a log.

ClinicQ is phone-first: a patient is known by a mobile number, signs in with a one-time code, and
the USSD gateway reports every session with the caller's MSISDN. Any of those, written to a log, is
personal information copied to a place POPIA does not expect it, and read by people who have no
need of it. Masking at each call site cannot hold: the next ``logger.info(f"... {phone}")`` in any
module leaks. So :class:`RedactionFilter` sits on **every handler** ``setup_logging`` installs
(console and S3) and rewrites each record before it is formatted, whichever module logged it:

* the message, after its ``%`` arguments are merged in, so ``logger.info("sms to %s", phone)`` is
  covered as well as an f-string;
* any string ``extra=`` field, and the value of any field *named* like a secret or a phone
  (``phone``, ``msisdn``, ``otp``, ``code``, ``password``, ``token``…), whatever it looks like;
* the formatted traceback, because an exception's message can quote its input.

What is masked, and why these shapes:

* **Phone numbers:** South African numbers written locally (``082 123 4567``, ``0821234567``),
  internationally (``+27 82 123 4567``, ``0027…``) or as an MSISDN (``27821234567``, what a USSD
  gateway sends), and any other ``+``-prefixed international number. Deliberately not "any long
  digit run": ticket ids, epoch timestamps and counts must stay readable.
* **OTPs:** a 4 to 8 digit number that follows a word saying it is one (``OTP``, ``code``,
  ``PIN``, ``passcode``, ``one-time``…). A bare six-digit number is left alone for the same reason.
* **Tokens:** JWTs (``eyJ…``) and ``Bearer`` credentials.

Redaction is best effort by pattern, and a backstop, not a licence: the rule is still to log ids,
never personal data (see the audit trail, Issue 20, for who did what).
"""

import logging
import re
from collections.abc import Mapping
from typing import Any, Final

#: What replaces each kind of secret. Named, so a reader of the log knows something was there.
PHONE_MASK: Final = "[REDACTED:phone]"
OTP_MASK: Final = "[REDACTED:otp]"
TOKEN_MASK: Final = "[REDACTED:token]"
SECRET_MASK: Final = "[REDACTED]"

_SEP = r"[ \-.]?"

#: Phone numbers, most specific first. Each is bounded so it never eats part of a longer token.
_PHONE_PATTERNS: Final = (
    # South Africa, international or MSISDN form: +27 / 0027 / 27, then 9 digits.
    re.compile(
        rf"(?<![\w+])(?:\+|00)?27{_SEP}\(?0?\)?{_SEP}\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{4}}(?!\w)"
    ),
    # South Africa, local form: a 0 trunk prefix, then 9 digits (082 123 4567, 011 555 1234).
    re.compile(rf"(?<![\w+])0\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{4}}(?!\w)"),
    # Any other E.164 number written with its +: 8 to 15 digits.
    re.compile(r"(?<![\w+])\+[1-9](?:[ \-.]?\d){7,14}(?!\w)"),
)

#: A one-time code: a word that names it, up to three short words ("is", "for you"), then the
#: 4 to 8 digits. Only the digits go; the label stays, so the line still says what was sent.
_OTP = re.compile(
    r"(?i)\b(otp|one[- ]time(?: pin| password| code)?|verification code|security code|"
    r"passcode|pin|code)\b(\W+(?:[a-z]{1,8}\W+){0,3}?)\d{4,8}\b"
)

#: The secret in an SMS delivery-receipt callback path (Issue 65): the gateway cannot sign, so the URL
#: carries it, and a request log line must not.
_WEBHOOK_PATH_TOKEN = re.compile(r"(/webhooks/sms/[\w-]+/)[^/\s?\"']+")

#: A JSON Web Token: three base64url segments, the first always starting ``eyJ`` (``{"``).
_JWT = re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*")
#: An HTTP bearer credential, whatever its format.
_BEARER = re.compile(r"(?i)\b(bearer)\s+[\w.~+/-]+=*")

#: Field names whose value is always a secret or a phone, whatever it looks like. Compared
#: lowercase, with the name split on ``_``/``-`` so ``patient_phone`` and ``otp-code`` count.
_PHONE_KEYS: Final = frozenset({"phone", "msisdn", "mobile", "cell", "cellphone"})
_SECRET_KEYS: Final = frozenset(
    {"otp", "pin", "passcode", "password", "secret", "token", "authorization"}
)
#: ``code`` alone is a secret (a sign-in code); ``error_code`` and ``status_code`` are not.
_SECRET_EXACT_KEYS: Final = frozenset({"code"})

#: ``LogRecord`` attributes the logging module owns; everything else on a record is ``extra=``.
_RECORD_ATTRS: Final = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
}


def redact(text: str) -> str:
    """Return ``text`` with phone numbers, OTPs and tokens masked."""
    text = _WEBHOOK_PATH_TOKEN.sub(lambda m: f"{m.group(1)}{TOKEN_MASK}", text)
    text = _JWT.sub(TOKEN_MASK, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {TOKEN_MASK}", text)
    text = _OTP.sub(lambda m: f"{m.group(1)}{m.group(2)}{OTP_MASK}", text)
    for pattern in _PHONE_PATTERNS:
        text = pattern.sub(PHONE_MASK, text)
    return text


def _key_parts(key: str) -> set[str]:
    """The words of a field name: ``patient_phone`` -> ``{"patient", "phone"}``."""
    return set(re.split(r"[_\-\s.]+", key.lower()))


def redact_value(key: str, value: Any) -> Any:
    """Redact one structured field: by its name first, then by what its text contains.

    A field named like a phone or a secret is masked whole, even if its value would not match a
    pattern (a code logged as an int, a number with odd punctuation). Mappings and sequences are
    walked, so a nested payload is covered too. Other non-string values pass through.
    """
    parts = _key_parts(key)
    if value is not None and parts & _PHONE_KEYS:
        return PHONE_MASK
    if value is not None and (
        parts & _SECRET_KEYS or key.lower() in _SECRET_EXACT_KEYS
    ):
        return SECRET_MASK
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact_value(key, v) for v in value)
    return value


class RedactionFilter(logging.Filter):
    """Mask phone numbers, OTPs and tokens on every record a handler emits.

    Attach it to the **handler**, not a logger: a handler filter sees every record that reaches
    it, including those propagated from module loggers, whereas a logger's filter sees only records
    logged on that logger itself. It never drops a record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record in place (message, extra fields, traceback), then keep it."""
        try:
            message = record.getMessage()
        except Exception:  # a broken %-format must not lose the line
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        for name, value in list(vars(record).items()):
            if name not in _RECORD_ATTRS and not name.startswith("_"):
                setattr(record, name, redact_value(name, value))
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
