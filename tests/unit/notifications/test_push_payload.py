"""A web push says the ticket number and the clinic's name, and nothing else (Issue 64).

A lock screen is read by whoever picks the phone up. So for every patient message, rendered for web push
from a context full of things that must not appear (a queue called "HIV clinic", a room, the reason for
the visit, the patient's name, a phone number), the payload holds only :data:`PAYLOAD_KEYS`, and none of
those values is anywhere in it. A renderer that started using ``{queue}`` would fail here.
"""

from __future__ import annotations

import json

import pytest

from src.commons.enums import PATIENT_EVENT_TEMPLATE, NotificationChannel, PatientEvent
from src.modules.notifications import templates
from src.modules.notifications.transports.webpush import PAYLOAD_KEYS, payload_for

_CONTEXT = {
    "number": "T004",
    "clinic": "Zola Community Clinic",
    "queue": "HIV clinic",
    "room": "Room 9",
    "reason": "chest pain",
    "name": "Nomvula Zwelithini",
    "phone": "+27825550900",
    "minutes": 5,
    "wait": "~15–25 min",
    "page_url": "/t/lyCty16hqVs1QSlnNhoh2gT1JY-5xgymJ6BMHC1XUfk",
}
_NEVER = ("HIV", "Room 9", "chest pain", "Nomvula", "+2782", "15–25")


@pytest.mark.parametrize("event", list(PatientEvent))
def test_a_push_payload_carries_only_the_number_and_the_clinic(
    event: PatientEvent,
) -> None:
    message = templates.render(
        NotificationChannel.WEB_PUSH, PATIENT_EVENT_TEMPLATE[event], _CONTEXT
    )
    payload = payload_for(message)
    assert set(payload) <= PAYLOAD_KEYS
    wire = json.dumps(payload, ensure_ascii=False)
    assert "T004" in payload["body"] and "Zola Community Clinic" in payload["body"]
    assert not [word for word in _NEVER if word in wire], wire
    assert payload["url"] == _CONTEXT["page_url"]


def test_a_link_that_is_not_a_ticket_page_is_never_put_in_a_payload() -> None:
    """The service worker opens ``url``: only a ticket page of this site, else the home page."""
    context = {**_CONTEXT, "page_url": "https://evil.example/t/x"}
    message = templates.render(
        NotificationChannel.WEB_PUSH, PATIENT_EVENT_TEMPLATE[PatientEvent.NEXT], context
    )
    assert payload_for(message)["url"] == "/"
