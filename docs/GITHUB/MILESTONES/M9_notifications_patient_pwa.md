# Milestone 9: Notifications & Patient PWA

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 9–10 · **Suggested tag:** `v0.9.0`
**Primary owner:** Backend (Integrations) Dev · Frontend (Patient) Dev
**Depends on:** M6
**Blocks:** M10 (channels reuse the notification service), M11 (reminders)

## Goal

Close the loop with the patient: a live ticket page that counts down, an installable PWA that still shows the last known position offline, and a notification service that reaches them by push, SMS or WhatsApp with preferences, quiet hours and cost guardrails respected.

## Why this milestone exists

The product's core promise is *"wait at home, not on a bench"*. That promise is only kept if the
"you're next" message actually arrives, which is why notifications are a **service with adapters and
a delivery log**, not a `send_sms()` call sprinkled through the queue code.

SMS costs real money per message on a student budget, so the send path carries a hard per-site daily
cap and prefers free transports (web push, WhatsApp session messages) before falling back to SMS.
Every send is recorded with its provider, cost and delivery status, which is also what makes the
no-show analysis in M12 meaningful: a high no-show rate caused by undelivered messages is a very
different problem from one caused by bad wait estimates.

## Scope

- Notification service with pluggable transports, a delivery log, retries and backoff
- Web Push (VAPID) subscription and the 'you're next' push
- SMS gateway adapter with delivery receipts, per-site daily cost caps and a kill switch
- Multi-language message templates with an admin editor and preview
- Patient preferences, quiet hours and opt-out handling that the sender must honour
- Patient ticket page: live position, ETA range countdown, cancel, directions
- PWA shell: manifest, service worker, offline last-known ticket, install prompt
- QR ticket code used for kiosk check-in (M11) and reception lookup
- Notification OpenAPI contract and delivery/failure tests

## Issues

| # | Title |
|---|-------|
| 63 | Notification service, transport adapters and delivery log |
| 64 | Web Push (VAPID) subscriptions and 'you're next' push |
| 65 | SMS gateway adapter with cost caps, delivery receipts and kill switch |
| 66 | Multi-language notification templates and admin editor |
| 67 | Notification preferences, quiet hours and opt-out enforcement |
| 68 | Patient ticket page: live position, ETA countdown, cancel |
| 69 | PWA shell: manifest, service worker, offline last-known ticket |
| 70 | QR ticket code for kiosk check-in and reception lookup |
| 71 | Notification OpenAPI contract, delivery and retry tests |

## Exit criteria

- [ ] A patient receives a 'you're next' message within 5 seconds of Call Next on their preferred transport
- [ ] The sender refuses to send outside quiet hours or after an opt-out, proven by tests
- [ ] SMS spend per site per day is capped, and hitting the cap raises an alert rather than failing silently
- [ ] The PWA installs to a home screen and shows the last known ticket position with no network
- [ ] A failed send is retried with backoff and ends in the delivery log with a terminal status
- [ ] Every template renders correctly in all supported languages with no truncation on a 160-character SMS

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M9/)
