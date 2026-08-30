# Issue 64: Web Push (VAPID) subscriptions and 'you're next' push

**Area:** Backend / Notifications
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issue 63
**Estimate:** 2 days
**Status:** Planned

## Context

Web push is free, instant, and works from an installed PWA without an app store. It is the preferred
transport for smartphone patients, with SMS reserved as the paid fallback for everyone else.

## Scope

- VAPID key generation and configuration, with keys held as secrets
- Subscription endpoint storing the push subscription against the patient
- Push payloads for queue position updates, 'you are next', 'please come in now' and cancellation
- Subscription expiry handling and automatic cleanup of dead endpoints
- Permission-request UX that asks at the right moment: after joining, never on first page load

## Acceptance criteria

- [ ] An installed PWA receives a push within 5 seconds of Call Next
- [ ] A dead or expired subscription is removed automatically
- [ ] The permission prompt appears only after a patient joins a queue
- [ ] Payloads carry no clinical detail beyond the ticket number and clinic name
- [ ] Push works on Android Chrome and desktop; iOS behaviour is documented with its limitations
- [ ] Declining push falls back to SMS if a phone number is present

## Files touched

- `app/services/transports/webpush.py`
- `app/api/push.py`
- `app/static/js/push.js`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #64
