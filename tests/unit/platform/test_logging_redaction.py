"""Redaction happens in the logging layer, for every module (Issue 6).

The proof the issue asks for is the first test: the real ``setup_logging()`` is run, and a phone
number is logged from loggers of modules that do not exist yet, the way a future patients or USSD
module would, with no call-site masking at all. What reaches stdout must be masked. The rest pins
the patterns (what is masked, and what must stay readable) and the S3 handler's filter.
"""

import io
import json
import logging
from collections.abc import Iterator

import pytest

import src.core.logging_config as logging_config
from src.commons.enums import LogFormat
from src.core.config import Settings
from src.core.log_redaction import (
    OTP_MASK,
    PHONE_MASK,
    SECRET_MASK,
    TOKEN_MASK,
    RedactionFilter,
    redact,
    redact_value,
)

_PHONE = "+27 82 123 4567"
_DIGITS = "821234567"
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwYXRpZW50In0.c2lnbmF0dXJlLWJ5dGVz"


@pytest.fixture
def root_logging() -> Iterator[None]:
    """Leave the root logger exactly as the test found it."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _json_lines(text: str) -> list[dict[str, object]]:
    """Every line of console output, parsed: each one must be a JSON object."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_a_phone_number_logged_from_any_module_comes_out_masked(
    root_logging: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real setup, three modules, four ways of logging a number: none reaches the output."""
    monkeypatch.setattr(
        logging_config,
        "get_settings",
        lambda: Settings(_env_file=None, log_format=LogFormat.JSON),
    )
    monkeypatch.setattr(logging_config, "create_s3_handler_if_enabled", lambda: None)
    logging_config.setup_logging()

    patients = logging.getLogger("src.modules.patients.service")
    ussd = logging.getLogger("src.modules.channels.ussd")
    notifications = logging.getLogger("src.modules.notifications.sms")

    patients.info(f"Verified patient {_PHONE}")
    ussd.warning("USSD session from %s timed out", "27821234567")
    notifications.info(
        "Queued SMS", extra={"recipient_phone": _PHONE, "note": f"to {_PHONE}"}
    )
    try:
        raise ValueError(f"no patient with number {_PHONE}")
    except ValueError:
        patients.exception("Lookup failed")

    output = capsys.readouterr().out
    lines = _json_lines(output)
    assert len(lines) == 4
    assert _DIGITS not in output
    assert "821234567" not in output and "82 123 4567" not in output
    assert lines[0]["message"] == f"Verified patient {PHONE_MASK}"
    assert lines[1]["message"] == f"USSD session from {PHONE_MASK} timed out"
    assert lines[2]["extra"] == {
        "recipient_phone": PHONE_MASK,
        "note": f"to {PHONE_MASK}",
    }
    assert PHONE_MASK in str(lines[3]["exception"])


def test_an_otp_and_a_token_are_masked_too(
    root_logging: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Through the same real setup: the code and the bearer token are gone, the words stay."""
    monkeypatch.setattr(
        logging_config,
        "get_settings",
        lambda: Settings(_env_file=None, log_format=LogFormat.JSON),
    )
    monkeypatch.setattr(logging_config, "create_s3_handler_if_enabled", lambda: None)
    logging_config.setup_logging()

    logging.getLogger("src.modules.patients.otp").info(
        "Your ClinicQ code is %s", "482913", extra={"otp": "482913"}
    )
    logging.getLogger("src.api").warning(f"Rejected Authorization: Bearer {_JWT}")  # noqa: G004

    output = capsys.readouterr().out
    first, second = _json_lines(output)
    assert "482913" not in output and _JWT not in output
    assert first["message"] == f"Your ClinicQ code is {OTP_MASK}"
    assert first["extra"] == {"otp": SECRET_MASK}
    assert second["message"] == f"Rejected Authorization: Bearer {TOKEN_MASK}"


def test_text_format_is_redacted_the_same_way(root_logging: None) -> None:
    """``LOG_FORMAT=text`` goes through the same filter."""
    stream = io.StringIO()
    logging.getLogger().handlers[:] = [
        logging_config.build_console_handler(LogFormat.TEXT, logging.INFO, stream)
    ]
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("src.anywhere").info("call %s", "082 123 4567")
    assert stream.getvalue().rstrip().endswith(f"call {PHONE_MASK}")


def test_the_s3_handler_carries_the_redaction_filter(
    root_logging: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Records shipped to S3 are masked as well: the filter is on that handler too."""
    shipped: list[logging.LogRecord] = []

    class _FakeS3Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            shipped.append(record)

    monkeypatch.setattr(
        logging_config,
        "get_settings",
        lambda: Settings(_env_file=None, log_format=LogFormat.JSON),
    )
    monkeypatch.setattr(
        logging_config, "create_s3_handler_if_enabled", lambda: _FakeS3Handler()
    )
    logging_config.setup_logging()

    logging.getLogger("src.modules.channels.ussd").error("gateway rejected %s", _PHONE)

    assert [r.getMessage() for r in shipped] == [f"gateway rejected {PHONE_MASK}"]


@pytest.mark.parametrize(
    "text",
    [
        "+27 82 123 4567",
        "+27821234567",
        "0027 82 123 4567",
        "27821234567",
        "082 123 4567",
        "082-123-4567",
        "0821234567",
        "011 555 1234",
        "+27 (0)82 123 4567",
        "+44 20 7946 0958",
        "+1 415 555 2671",
    ],
)
def test_phone_numbers_in_every_usual_shape_are_masked(text: str) -> None:
    """Local, international, MSISDN and foreign forms."""
    assert redact(f"number {text} end") == f"number {PHONE_MASK} end"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your OTP is 123456", f"Your OTP is {OTP_MASK}"),
        ("otp=123456", f"otp={OTP_MASK}"),
        ("verification code: 4821", f"verification code: {OTP_MASK}"),
        (
            "Your sign-in code for today is 48219370",
            f"Your sign-in code for today is {OTP_MASK}",
        ),
        ("PIN 9876", f"PIN {OTP_MASK}"),
    ],
)
def test_otps_are_masked_but_their_label_stays(text: str, expected: str) -> None:
    """The digits go; the line still says what kind of thing was there."""
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "ticket A017 joined at 2026-09-11T07:45:58+02:00",
        "id 01a08ecc-dd1f-7350-9d45-9273b6e70647",
        "epoch 1789102185759 ms",
        "HTTP 404 GET /api/v1/queues/42",
        "waited 123456 ms for 3 tickets",
        "client 10.0.0.12 port 5432",
        "Domain error queue.ticket.not_found answered 409",
    ],
)
def test_ordinary_numbers_stay_readable(text: str) -> None:
    """Ids, timestamps, durations, IPs and status codes are not phone numbers or codes."""
    assert redact(text) == text


def test_fields_are_masked_by_name_whatever_their_value() -> None:
    """A phone or secret field is masked whole; an error code or status code is not a secret."""
    assert redact_value("patient_phone", 821234567) == PHONE_MASK
    assert redact_value("msisdn", "anything") == PHONE_MASK
    assert redact_value("code", 4821) == SECRET_MASK
    assert redact_value("refresh_token", "opaque") == SECRET_MASK
    assert (
        redact_value("error_code", "queue.ticket.not_found") == "queue.ticket.not_found"
    )
    assert redact_value("status_code", 404) == 404
    assert redact_value("payload", {"msisdn": "x", "note": f"call {_PHONE}"}) == {
        "msisdn": PHONE_MASK,
        "note": f"call {PHONE_MASK}",
    }


def test_the_filter_never_drops_a_record_even_with_a_broken_format() -> None:
    """A record whose %-arguments do not fit is still written, and still redacted."""
    record = logging.makeLogRecord({"msg": f"to {_PHONE} %s %s", "args": ("one",)})
    assert RedactionFilter().filter(record) is True
    assert record.getMessage() == f"to {PHONE_MASK} %s %s"


def test_patient_free_text_is_screened_for_phones_emails_and_id_numbers_and_counted() -> (
    None
):
    """Issue 87: a feedback comment keeps its words and loses what could identify or reach the patient."""
    from src.core.log_redaction import screen_free_text

    screened, removed = screen_free_text(
        "Kind sister. Phone 082 123 4567, mail thandi.m@example.co.za, ID 800101 5009 087."
    )
    assert screened == (
        "Kind sister. Phone [REDACTED:phone], mail [REDACTED:email], ID [REDACTED:id]."
    )
    assert removed == 3
    assert screen_free_text("Waited 45 minutes, room 4 was quick.") == (
        "Waited 45 minutes, room 4 was quick.",
        0,
    )
