# Issue 71: Notification OpenAPI contract, delivery and retry tests

**Area:** Backend / Quality
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 63–70
**Estimate:** 2 days
**Status:** Planned

## Context

Notifications are the module most likely to fail silently in production: a message that is never sent
looks exactly like a message nobody replied to. The contract and the failure-path tests exist to make
that failure loud.

## Scope

- Hand-written `contracts/notifications.yaml` covering subscription, preferences and the admin views
- Drift test between contract and routers
- Failure-path tests: provider timeout, provider error, malformed number, revoked subscription
- Idempotency test proving one queue event never produces two messages
- A delivery-rate dashboard panel showing sends, failures and cost per transport

## Acceptance criteria

- [ ] The contract documents every notification route including error responses
- [ ] Every failure path ends in a terminal log status, never in limbo
- [ ] A replayed queue event sends exactly one notification
- [ ] Delivery rate and cost per transport are visible on a dashboard
- [ ] Tests run with no real provider credentials
- [ ] An alert fires when the delivery failure rate crosses a threshold

## Files touched

- `contracts/notifications.yaml`
- `tests/integration/test_notification_failures.py`
- `tests/test_openapi_contracts.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #71
