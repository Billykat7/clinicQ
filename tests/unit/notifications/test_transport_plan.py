"""Which transport a patient notification tries, in which order (Issue 63).

Pure logic, so a unit test: the patient's preferred transport first when it can reach them, then
every free transport before any paid one, skipping a transport with no address, and never trying a
transport twice down a fallback chain. Also that every patient event has a template, and every
template renders on every patient channel, so a fallback never meets a message it cannot word.
"""

from __future__ import annotations

from src.commons.enums import (
    PATIENT_EVENT_TEMPLATE,
    NotificationChannel,
    PatientEvent,
)
from src.modules.notifications import template_registry, templates
from src.modules.notifications.service import plan_transports
from src.modules.notifications.transports import NoopTransport, PatientAddresses

_PATIENT = PatientAddresses(patient_id="p-1", phone_e164="+27820000001")
_CONTEXT = {
    "number": "T001",
    "clinic": "Zola Clinic",
    "queue": "Triage",
    "room": "Room 2",
    "minutes": 5,
    "wait": "15–25 min",
}


def _set(*transports: NoopTransport) -> dict[NotificationChannel, NoopTransport]:
    return {transport.channel: transport for transport in transports}


def _push(address: str | None = "sub") -> NoopTransport:
    return NoopTransport(
        channel=NotificationChannel.WEB_PUSH, free=True, address=address
    )


def _whatsapp(address: str | None = "wa") -> NoopTransport:
    return NoopTransport(
        channel=NotificationChannel.WHATSAPP, free=True, address=address
    )


def _sms() -> NoopTransport:
    return NoopTransport(channel=NotificationChannel.SMS, address="+27820000001")


def _channels(plan: list[tuple[NotificationChannel, str]]) -> list[NotificationChannel]:
    return [channel for channel, _address in plan]


def test_free_transports_come_before_sms() -> None:
    plan = plan_transports(_PATIENT, _set(_sms(), _whatsapp(), _push()))
    assert _channels(plan) == [
        NotificationChannel.WEB_PUSH,
        NotificationChannel.WHATSAPP,
        NotificationChannel.SMS,
    ]


def test_a_free_transport_is_ordered_by_what_it_costs_not_by_its_name() -> None:
    """If WhatsApp ever charged, it would drop behind every free transport."""
    paid_whatsapp = NoopTransport(channel=NotificationChannel.WHATSAPP, free=False)
    plan = plan_transports(_PATIENT, _set(_sms(), paid_whatsapp, _push()))
    assert _channels(plan)[0] is NotificationChannel.WEB_PUSH


def test_a_transport_that_cannot_reach_the_patient_is_left_out() -> None:
    plan = plan_transports(_PATIENT, _set(_push(address=None), _whatsapp(), _sms()))
    assert _channels(plan) == [NotificationChannel.WHATSAPP, NotificationChannel.SMS]


def test_the_preferred_transport_comes_first_when_it_can_reach_the_patient() -> None:
    transports = _set(_push(), _whatsapp(), _sms())
    plan = plan_transports(_PATIENT, transports, preferred=NotificationChannel.SMS)
    assert _channels(plan) == [
        NotificationChannel.SMS,
        NotificationChannel.WEB_PUSH,
        NotificationChannel.WHATSAPP,
    ]


def test_a_preference_that_cannot_reach_the_patient_falls_back_to_the_chain() -> None:
    transports = _set(_push(address=None), _sms())
    plan = plan_transports(_PATIENT, transports, preferred=NotificationChannel.WEB_PUSH)
    assert _channels(plan) == [NotificationChannel.SMS]


def test_a_fallback_never_retries_a_transport_already_tried() -> None:
    transports = _set(_push(), _whatsapp(), _sms())
    plan = plan_transports(
        _PATIENT,
        transports,
        exclude=frozenset({NotificationChannel.WEB_PUSH, NotificationChannel.WHATSAPP}),
    )
    assert _channels(plan) == [NotificationChannel.SMS]


def test_every_patient_event_has_a_template_that_renders_on_every_patient_channel() -> (
    None
):
    assert set(PATIENT_EVENT_TEMPLATE) == set(PatientEvent)
    for template in PATIENT_EVENT_TEMPLATE.values():
        for channel in (
            NotificationChannel.SMS,
            NotificationChannel.WEB_PUSH,
            NotificationChannel.WHATSAPP,
        ):
            message = templates.render(channel, template, _CONTEXT)
            # Every message about a ticket says its number; a collection reminder (Issue 85) is about
            # a repeat rather than a ticket, and has no number in its variables to say.
            if "number" in template_registry.VARIABLES[template]:
                assert "T001" in message.text, (channel, template)
            assert message.text.strip(), (channel, template)
