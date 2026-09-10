# Issue 64: Web Push (VAPID) subscriptions and 'you're next' push

> **In short:** A patient with the app installed gets "you're next" on their lock screen within seconds, for free.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Notifications |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log |
| **Unblocks** | [Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests |

## Context

Web push is free, instant, and works from an installed PWA without an app store. It is the preferred
transport for smartphone patients, with SMS reserved as the paid fallback for everyone else.

## Starting point

- Nothing for web push exists yet; a VAPID library (for example `pywebpush`) needs adding to `requirements.txt`.
- Keep the browser code in an external file under `src/static/js/` (CSP).

## Scope

- VAPID key generation and configuration, with keys held as secrets
- Subscription endpoint storing the push subscription against the patient
- Push payloads for queue position updates, 'you are next', 'please come in now' and cancellation
- Subscription expiry handling and automatic cleanup of dead endpoints
- Permission-request UX that asks at the right moment: after joining, never on first page load

## Out of scope

- The installable app shell (Issue 69).
- SMS fallback delivery (Issue 65 provides it; this issue triggers it).

## Acceptance criteria

- [ ] An installed PWA receives a push within 5 seconds of Call Next
- [ ] A dead or expired subscription is removed automatically
- [ ] The permission prompt appears only after a patient joins a queue
- [ ] Payloads carry no clinical detail beyond the ticket number and clinic name
- [ ] Push works on Android Chrome and desktop; iOS behaviour is documented with its limitations
- [ ] Declining push falls back to SMS if a phone number is present

## How to verify

1. Install the PWA on an Android phone, join a queue, call it from the dashboard: a push within 5 seconds.
2. Load the site fresh: no permission prompt until after joining.
3. Expire a subscription on the push service: the next send removes it.

## Files touched

- `src/modules/notifications/transports/webpush.py`
- `src/modules/notifications/router.py`
- `src/static/js/push.js`
- `requirements.txt`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #64
