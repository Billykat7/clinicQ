"""Every queue SMS fits one segment, in every language, however long a clinic's name is (Issue 65).

A gateway bills per segment. These tests pin the counting rules (GSM 7-bit with its two-septet extension
characters, UCS-2 for anything else), the normalisation that keeps a stray en dash from doubling a bill,
and then render **every** patient SMS in **every** language the registry has
(:data:`~src.modules.notifications.templates.SMS_LANGUAGES`) with the longest values the database allows,
and require one GSM 7-bit segment each.
"""

from __future__ import annotations

import pytest

from src.commons.enums import PATIENT_EVENT_TEMPLATE, NotificationChannel, PatientEvent
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import Ticket
from src.modules.notifications import templates
from src.modules.notifications.sms_segments import (
    SegmentCount,
    SmsEncoding,
    count_segments,
    to_gsm7,
)


def _longest(column: object) -> int:
    return int(column.property.columns[0].type.length)  # type: ignore[attr-defined]


#: The longest values the tables allow, and the longest wait label the estimator writes.
_WORST = {
    "number": "Z" * _longest(Ticket.number),
    "clinic": "Hillbrow Community Health Centre and Maternity Obstetric Unit " * 4,
    "queue": "Chronic medication collection and repeat scripts",
    "room": "Consulting room nine, second floor",
    "minutes": 60,
    "wait": "~180–240 min (approximate)",
    "page_url": "/t/" + "x" * 43,
}


def test_the_worst_case_values_really_are_the_longest_the_tables_allow() -> None:
    assert len(_WORST["clinic"]) >= _longest(Site.name)
    assert len(_WORST["queue"]) >= 40 and _longest(Queue.name) > 0


@pytest.mark.parametrize(
    ("text", "encoding", "units", "segments"),
    [
        ("a" * 160, SmsEncoding.GSM7, 160, 1),
        ("a" * 161, SmsEncoding.GSM7, 161, 2),
        ("a" * 306, SmsEncoding.GSM7, 306, 2),
        ("a" * 307, SmsEncoding.GSM7, 307, 3),
        (
            "{}" * 40,
            SmsEncoding.GSM7,
            160,
            1,
        ),  # extension characters take two septets each
        ("€" * 81, SmsEncoding.GSM7, 162, 2),
        ("ê" * 70, SmsEncoding.UCS2, 70, 1),
        ("ê" * 71, SmsEncoding.UCS2, 71, 2),
        (
            "a" * 134 + "ê",
            SmsEncoding.UCS2,
            135,
            3,
        ),  # one character makes the whole message UCS-2
        ("😀" * 35, SmsEncoding.UCS2, 70, 1),  # an emoji is two UTF-16 units
    ],
)
def test_segments_are_counted_the_way_a_gateway_bills_them(
    text: str, encoding: SmsEncoding, units: int, segments: int
) -> None:
    assert count_segments(text) == SegmentCount(encoding, units, segments)


def test_look_alike_characters_are_sent_as_gsm_so_a_dash_cannot_double_the_bill() -> (
    None
):
    line = "Wag ~15–25 min — “gou” … Jy’s volgende, sê die kliniek"
    assert count_segments(line).encoding is SmsEncoding.UCS2
    normalised = to_gsm7(line)
    assert normalised == 'Wag ~15-25 min - "gou" ... Jy\'s volgende, se die kliniek'
    assert count_segments(normalised).encoding is SmsEncoding.GSM7


@pytest.mark.parametrize("language", templates.SMS_LANGUAGES)
@pytest.mark.parametrize("event", list(PatientEvent))
def test_every_queue_sms_fits_one_segment_in_every_language(
    language: str, event: PatientEvent
) -> None:
    """How to verify, step 3: every template through the segment counter, one segment each."""
    message = templates.render(
        NotificationChannel.SMS, PATIENT_EVENT_TEMPLATE[event], dict(_WORST)
    )
    count = count_segments(to_gsm7(message.text))
    assert (count.encoding, count.segments) == (SmsEncoding.GSM7, 1), (
        f"{event.value} [{language}] is {count.units} {count.encoding.value} units: {message.text}"
    )
