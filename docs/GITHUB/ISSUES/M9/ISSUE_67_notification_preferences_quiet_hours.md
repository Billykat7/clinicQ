# Issue 67: Notification preferences, quiet hours and opt-out enforcement

> **In short:** Patients control how and when they hear from ClinicQ, and "stop" means stop, everywhere, straight away.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 6 (weeks 11–12) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Notifications |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 21](../M3/ISSUE_21_consent_capture_withdrawal.md): Consent capture and withdrawal (display, notifications, board comment)<br>[Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log |
| **Unblocks** | [Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests |

## Context

Consent is not a one-time checkbox: a patient can change their mind, and nobody should receive a
queue notification at 02:00. The sender must consult preferences and quiet hours before every send, and
that must be impossible to bypass.

## Starting point

- `src/modules/notifications/preferences.py` already gates every send (channel, quiet hours, unsubscribe), with the page at `/account/notifications`. It is built for account holders; patients have none, so preferences must also be reachable by ticket reference.
- Consent (Issue 21) and preferences are different things: consent is whether a message may be sent at all; preferences are how and when. The gate checks both.

## Scope

- Per-patient preferences: preferred transport, language, quiet-hours window, per-event opt-outs
- Global opt-out honoured across every channel and every event type
- Quiet hours suppressing non-urgent notifications, with a documented urgent exception ('you are next')
- Preference management from the ticket page, USSD, WhatsApp and a reply keyword
- A send-time gate that every transport must pass through

## Out of scope

- Consent capture (Issue 21).
- WhatsApp opt-in rules (Issue 76).

## Acceptance criteria

- [ ] A send outside the allowed window or after an opt-out is blocked, proven by a test
- [ ] Opting out via an SMS reply keyword takes effect immediately across all channels
- [ ] The urgent exception is narrowly defined, documented and tested
- [ ] Preferences are editable without an account, using the ticket reference
- [ ] No transport can dispatch without passing the gate, enforced by a guard test
- [ ] Opt-out state survives the patient joining a queue at a different clinic

## How to verify

1. Reply STOP to an SMS: the next message on every channel is blocked.
2. Queue a non-urgent message inside quiet hours: held; send "you are next": delivered.
3. Add a transport that skips the gate: the guard test fails.

## Files touched

- `src/modules/notifications/preferences.py`
- `src/modules/notifications/router.py`
- `tests/integration/notifications/test_quiet_hours.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #67
