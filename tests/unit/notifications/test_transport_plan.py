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
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.notifications.transports import (
    EmailTransport,
    NoopTransport,
    PatientAddresses,
)
from src.modules.notifications.transports.sms import SmsTransport
from src.modules.notifications.transports.webpush import WebPushTransport
from src.modules.notifications.transports.whatsapp import WhatsAppTransport

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


def _email(address: str | None = "nomsa@example.com") -> NoopTransport:
    return NoopTransport(channel=NotificationChannel.EMAIL, free=True, address=address)


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
        for channel in template_registry.PATIENT_CHANNELS:
            message = templates.render(channel, template, _CONTEXT)
            # Every message about a ticket says its number; a collection reminder (Issue 85) is about
            # a repeat rather than a ticket, and has no number in its variables to say.
            if "number" in template_registry.VARIABLES[template]:
                assert "T001" in message.text, (channel, template)
            assert message.text.strip(), (channel, template)


# --- email, and the patient who has nothing else (Issues 219, 220) -----------------------------
#
# These use the **real** adapters rather than NoopTransport: what is being asserted is precisely that
# each one reads the right field off PatientAddresses, and the double reports its configured address
# whoever the patient is.

_EMAIL_ONLY = PatientAddresses(patient_id="p-2", email="nomsa@example.com")
_BOTH = PatientAddresses(
    patient_id="p-3", phone_e164="+27820000001", email="nomsa@example.com"
)


def _real(*, email_configured: bool = True) -> dict[NotificationChannel, object]:
    """Every adapter as a deployment builds it, with no provider that touches a network."""
    return {
        NotificationChannel.WEB_PUSH: WebPushTransport(None),
        NotificationChannel.EMAIL: EmailTransport(configured=email_configured),
        NotificationChannel.WHATSAPP: WhatsAppTransport(),
        NotificationChannel.SMS: SmsTransport(FakeSmsProvider()),
    }


def test_email_comes_after_push_and_before_the_other_free_transport() -> None:
    """Both are free, so the order is about which is likelier to arrive: we always hold an address."""
    plan = plan_transports(_BOTH, _set(_sms(), _whatsapp(), _push(), _email()))
    assert _channels(plan) == [
        NotificationChannel.WEB_PUSH,
        NotificationChannel.EMAIL,
        NotificationChannel.WHATSAPP,
        NotificationChannel.SMS,
    ]


def test_a_patient_with_only_an_address_is_reachable_by_email_and_nothing_else() -> (
    None
):
    """Issue 220's whole reason: before it, this plan was empty and the message went nowhere."""
    plan = plan_transports(_EMAIL_ONLY, _real())
    assert _channels(plan) == [NotificationChannel.EMAIL]


def test_without_the_email_adapter_a_patient_with_only_an_address_reaches_nothing() -> (
    None
):
    """The state of the world before this issue, kept as the reason the adapter exists."""
    without_email = {
        channel: transport
        for channel, transport in _real().items()
        if channel is not NotificationChannel.EMAIL
    }
    assert _channels(plan_transports(_EMAIL_ONLY, without_email)) == []


def test_email_is_left_out_when_the_deployment_cannot_send_mail() -> None:
    """No SMTP server means no address to offer, so the chain passes over it without failing it."""
    plan = plan_transports(_BOTH, _real(email_configured=False))
    assert NotificationChannel.EMAIL not in _channels(plan)
    assert _channels(plan) == [NotificationChannel.SMS]


def test_a_patient_who_prefers_email_gets_it_first_and_sms_stays_last() -> None:
    plan = plan_transports(
        _BOTH,
        _set(_push(), _sms(), _email()),
        preferred=NotificationChannel.EMAIL,
    )
    assert _channels(plan)[0] is NotificationChannel.EMAIL
    assert _channels(plan)[-1] is NotificationChannel.SMS
