# Issue 63: Notification service, transport adapters and delivery log

> **In short:** The queue engine says "tell this patient they are next" in one call, and this service works out how, sends it, retries it, and records what happened.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Notifications |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 21](../M3/ISSUE_21_consent_capture_withdrawal.md): Consent capture and withdrawal (display, notifications, board comment)<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 64](../M9/ISSUE_64_web_push_vapid.md): Web Push (VAPID) subscriptions and 'you're next' push<br>[Issue 65](../M9/ISSUE_65_sms_gateway_cost_caps.md): SMS gateway adapter with cost caps, delivery receipts and kill switch<br>[Issue 66](../M9/ISSUE_66_notification_templates_i18n.md): Multi-language notification templates and admin editor<br>[Issue 67](../M9/ISSUE_67_notification_preferences_quiet_hours.md): Notification preferences, quiet hours and opt-out enforcement<br>[Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests<br>[Issue 82](../M11/ISSUE_82_appointment_reminders_reply.md): Appointment reminders with confirm/cancel by reply<br>[Issue 85](../M11/ISSUE_85_chronic_repeat_reminders.md): Chronic and repeat-visit reminder schedules<br>[Issue 87](../M11/ISSUE_87_post_visit_feedback.md): Post-visit feedback survey and satisfaction reporting |

## Context

The product's core promise is "wait at home, not on a bench", and that promise is only kept if the
"you're next" message actually arrives. Notifications are therefore a service with pluggable transports
and a delivery log, not a `send_sms()` call scattered through the queue code.

## Starting point

- Much of this exists in `src/modules/notifications/`: a delivery ledger (the `notification` table), retries with exponential backoff and a dead-letter state run by the scheduler's retry sweep, and email and SMS delivery.
- What is missing: patients (who have no account) as recipients, the web push and WhatsApp transports, the free-first fallback chain, and a cost column. Extend the module; do not start a second notification service.
- Retries run on APScheduler (`src/core/scheduler.py`), not `arq`; see [open decisions](../README.md#open-decisions).
- B builds the skeleton against the contract stub from sprint 3, before the queue engine exists.

## Scope

- `notify(patient, event, context)` service selecting a transport from patient preference and availability
- Transport adapter interface with implementations for web push, SMS and WhatsApp, plus a no-op for tests
- `notification_log`: patient, event, transport, provider message id, status, cost, timestamps
- Retry with exponential backoff and a terminal failure state, driven by `arq`
- Transport fallback chain: free transports first, SMS last

## Out of scope

- Each transport's provider details (Issues 64, 65, 76).
- Templates and preferences (Issues 66, 67).

## Acceptance criteria

- [ ] Every send is recorded in the log with a terminal status
- [ ] A failed send retries with backoff and stops at a documented maximum
- [ ] Transport selection honours patient preference before the fallback chain
- [ ] The queue engine calls one function and knows nothing about transports
- [ ] Adapters are swappable in tests without touching a real provider
- [ ] Sends never block a queue transition: dispatch is post-commit and best-effort

## How to verify

1. `pytest tests/unit/notifications tests/integration/notifications` stays green after the changes.
2. Make a transport fail twice then succeed: three attempts in the ledger with backoff, final status `sent`.
3. Call a ticket while the SMS provider is down: the queue transition still commits.

## Files touched

- `src/modules/notifications/service.py`
- `src/modules/notifications/transports/`
- `src/database/models/notification.py`
- `alembic/versions/NNNN_notification_patient_cost.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #63
