# Issue 63: Notification service, transport adapters and delivery log

**Area:** Backend / Notifications
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 41, 21
**Estimate:** 3 days
**Status:** Planned

## Context

The product's core promise is "wait at home, not on a bench", and that promise is only kept if the
"you're next" message actually arrives. Notifications are therefore a service with pluggable transports
and a delivery log, not a `send_sms()` call scattered through the queue code.

## Scope

- `notify(patient, event, context)` service selecting a transport from patient preference and availability
- Transport adapter interface with implementations for web push, SMS and WhatsApp, plus a no-op for tests
- `notification_log`: patient, event, transport, provider message id, status, cost, timestamps
- Retry with exponential backoff and a terminal failure state, driven by `arq`
- Transport fallback chain: free transports first, SMS last

## Acceptance criteria

- [ ] Every send is recorded in the log with a terminal status
- [ ] A failed send retries with backoff and stops at a documented maximum
- [ ] Transport selection honours patient preference before the fallback chain
- [ ] The queue engine calls one function and knows nothing about transports
- [ ] Adapters are swappable in tests without touching a real provider
- [ ] Sends never block a queue transition: dispatch is post-commit and best-effort

## Files touched

- `app/services/notifications.py`
- `app/services/transports/`
- `app/database/models/notification_log.py`
- `workers/notifications_worker.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #63
