# Issue 65: SMS gateway adapter with cost caps, delivery receipts and kill switch

> **In short:** SMS reaches the patients push cannot, without ever running up a bill nobody agreed to.

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

SMS is the only transport that reaches every patient, and the only one that costs money per message.
On a student budget an unbounded send loop is a real financial risk, so the cap, the kill switch and the
cost log are part of the feature rather than an afterthought.

## Starting point

- `src/modules/notifications/sms.py` already defines the `SmsProvider` interface with logging and fake providers (`SmsProviderKind`). Add an Africa's Talking-class provider as a third implementation.
- Delivery receipts arrive as signed webhooks: follow the pattern in `src/api/v1/routes/webhooks.py` (raw-body signature check, idempotent event rows).

## Scope

- SMS provider adapter (Africa's Talking-class) with delivery receipts and a sandbox mode
- Per-site daily message cap and a per-patient daily cap, enforced before dispatch
- Global kill switch that stops all SMS immediately without a deploy
- Cost recorded per message, with a running per-site total for the M12 reports
- Message length and encoding handling so no message silently splits into three billable parts

## Out of scope

- Template wording and translations (Issue 66).
- Cost reporting screens (Issue 90 reads the recorded cost).

## Acceptance criteria

- [ ] Hitting a cap stops sends and raises an alert rather than failing silently
- [ ] The kill switch stops all SMS within one minute, without a deploy
- [ ] Delivery receipts update the notification log to a terminal status
- [ ] Every message's cost is recorded and reportable per site
- [ ] Messages stay within one SMS segment, verified for every template
- [ ] Sandbox mode allows full local development with no spend

## How to verify

1. Set a site's daily cap to 3 and send 4: the fourth is blocked and an alert fires.
2. Flip the kill switch: no SMS leaves within one minute, with no deploy.
3. Run every template through the segment counter in all five languages: one segment each.

## Files touched

- `src/modules/notifications/sms.py`, `src/modules/notifications/sms_segments.py`
- `src/modules/notifications/budget.py`, `src/modules/notifications/budget_router.py`
- `src/api/v1/routes/webhooks.py`, `src/core/webhook_gateways/africastalking.py`
- `alembic/versions/0034_sms_budget.py`
- `docs/OPS/SMS_GATEWAY.md`
- `tests/integration/notifications/test_sms_caps.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/09-pricing-and-budget.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #65
