# Issue 71: Notification OpenAPI contract, delivery and retry tests

> **In short:** The notification behaviour is written down as a contract and tested on every way it can fail, so a message is never lost silently or sent twice.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 11–12 (weeks 21–24), with E on the same issue |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log<br>[Issue 64](../M9/ISSUE_64_web_push_vapid.md): Web Push (VAPID) subscriptions and 'you're next' push<br>[Issue 65](../M9/ISSUE_65_sms_gateway_cost_caps.md): SMS gateway adapter with cost caps, delivery receipts and kill switch<br>[Issue 66](../M9/ISSUE_66_notification_templates_i18n.md): Multi-language notification templates and admin editor<br>[Issue 67](../M9/ISSUE_67_notification_preferences_quiet_hours.md): Notification preferences, quiet hours and opt-out enforcement<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel<br>[Issue 69](../M9/ISSUE_69_pwa_shell_service_worker.md): PWA shell: manifest, service worker, offline last-known ticket<br>[Issue 70](../M9/ISSUE_70_qr_ticket_code.md): QR ticket code for kiosk check-in and reception lookup |
| **Unblocks** | No other issue waits on this one. |

## Context

Notifications are the module most likely to fail silently in production: a message that is never sent
looks exactly like a message nobody replied to. The contract and the failure-path tests exist to make
that failure loud.

## Starting point

- Reuse the contract drift test from Issue 30.
- The kernel's notification tests (`tests/unit/notifications/`, `tests/integration/notifications/`) already use the fake SMS provider; extend them rather than starting over.

## Scope

- Hand-written `contracts/notifications.yaml` covering subscription, preferences and the admin views
- Drift test between contract and routers
- Failure-path tests: provider timeout, provider error, malformed number, revoked subscription
- Idempotency test proving one queue event never produces two messages
- A delivery-rate dashboard panel showing sends, failures and cost per transport

## Out of scope

- Channel parity (Issue 79).

## Acceptance criteria

- [ ] The contract documents every notification route including error responses
- [ ] Every failure path ends in a terminal log status, never in limbo
- [ ] A replayed queue event sends exactly one notification
- [ ] Delivery rate and cost per transport are visible on a dashboard
- [ ] Tests run with no real provider credentials
- [ ] An alert fires when the delivery failure rate crosses a threshold

## How to verify

1. Simulate a provider timeout, an error, a malformed number and a revoked subscription: each ends in a terminal ledger status.
2. Replay one queue event: exactly one message.
3. Run the suite with no provider credentials set: green.

## Files touched

- `contracts/notifications.yaml`
- `tests/integration/notifications/test_notification_failures.py`
- `tests/integration/contracts/test_openapi_contracts.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #71
