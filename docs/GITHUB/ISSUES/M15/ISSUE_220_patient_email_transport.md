# Issue 220: Email as a patient notification transport

> **In short:** A patient who signed in with an email address and has no phone number still gets their "you are next" message — by email, free, ahead of SMS in the fallback chain.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 15 (weeks 29–30) |
| **Owner** | B, Backend/Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Notifications |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 63](../M9/ISSUE_63_notification_service_adapters.md): the notification service and its transports<br>[Issue 219](ISSUE_219_patient_email_sign_in.md): `patient.email` |
| **Unblocks** | Nothing. |

## Context

`NotificationChannel.EMAIL` exists and its docstring says why it is not in the patient chain:
*"`EMAIL` is for account holders only: a patient has no email address on record."* [Issue 219](ISSUE_219_patient_email_sign_in.md)
makes that untrue. Without this issue an email-only patient joins a queue and is then silent: the
chain is `WEB_PUSH → WHATSAPP → SMS`, `_addresses` reads a number that is `NULL`, `plan_transports`
leaves every transport out, and the message is recorded with nothing to send it through.

This is the smaller half of the pair, and the transport seam is already the right shape: a
`Transport` knows only whether it can reach a patient and how to hand one message over.

## Starting point

- `src/modules/notifications/transports/base.py`: `PatientAddresses` (`phone_e164`, `whatsapp_id`,
  `push_targets`) and the `Transport` ABC (`address_for`, `send`, `channel`, `free`).
- `src/modules/notifications/transports/registry.py`: `build_transports` wires web push, WhatsApp and
  SMS; `use_transports` is the test seam.
- `src/commons/enums.py`: `PATIENT_TRANSPORT_CHAIN`, and `NotificationChannel.EMAIL`'s docstring.
- `src/modules/notifications/service.py`: `_addresses`, `reachable_patient` (a dependant's messages go
  to their proxy, Issue 84), `plan_transports`, `deliver_email` (the account-holder path).
- `src/modules/notifications/templates.py`: per-channel rendering; email templates already exist for
  staff.

## Scope

- `EmailTransport` (`channel=EMAIL`, `free=True`), addressing `PatientAddresses.email`, sending through
  the same SMTP path `src/core/email_send.py` uses, mapping a rejected address to
  `PermanentTransportError` and a refused connection to `TransportError`.
- `PatientAddresses.email`, filled by `_addresses` from `reachable_patient` — so a dependant's email
  message goes to the proxy's address, the same redirection the phone gets.
- `PATIENT_TRANSPORT_CHAIN` becomes `WEB_PUSH → EMAIL → WHATSAPP → SMS`: free transports before the
  paid one, and email before WhatsApp because we hold an address and only sometimes a WhatsApp id.
- The patient templates rendered for `EMAIL` (queued, called, ready, cancelled, reminders), in the
  channel's own words with a subject line — not an SMS body in an envelope.
- `NotificationChannelPreference` and the patient's preferences accept `email` for a patient who has
  an address; quiet hours, opt-outs and the consent gate apply unchanged.
- `NotificationChannel.EMAIL`'s docstring, and every comment that says a patient has no address,
  corrected.

## Out of scope

- Charging or capping email the way `sms_budget` caps SMS: email is free and uncapped here.
- A patient changing their notification address.
- Marketing or digest email of any kind. Queue messages only.

## Acceptance criteria

- [ ] A patient with an address and no number, and no push subscription, receives a queue notification
      by email, and the ledger row records `channel=email` with its provider id
- [ ] A patient with a number and an address is reached by the chain's order — push, then email,
      **before** SMS — so a reachable patient never costs an SMS
- [ ] A dependant's email goes to their proxy's address (`reachable_patient`), never to the dependant
- [ ] A patient who has switched a non-essential category off is not emailed for it, and quiet hours
      hold an email exactly as they hold an SMS
- [ ] A permanently rejected address ends the row at once and the next transport is tried; a refused
      connection retries with backoff
- [ ] `tests/contract` still passes: no existing phone-patient delivery changes its channel

## How to verify

1. `TZ=UTC pytest tests/unit/notifications tests/integration/notifications tests/contract`
2. `make check`
3. With `PATIENT_EMAIL_SIGN_IN_ENABLED=true` and the dev outbox, join a queue as an email-only patient
   and call the ticket; the message lands in the outbox.

## Files touched

- `src/modules/notifications/transports/{base,email,registry,__init__}.py`
- `src/modules/notifications/{service,templates,patient_preferences}.py`
- `src/commons/enums.py`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #220
