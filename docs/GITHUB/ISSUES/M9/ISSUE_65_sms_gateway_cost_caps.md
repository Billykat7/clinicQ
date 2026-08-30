# Issue 65: SMS gateway adapter with cost caps, delivery receipts and kill switch

**Area:** Backend / Notifications
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issue 63
**Estimate:** 2 days
**Status:** Planned

## Context

SMS is the only transport that reaches every patient, and the only one that costs money per message.
On a student budget an unbounded send loop is a real financial risk, so the cap, the kill switch and the
cost log are part of the feature rather than an afterthought.

## Scope

- SMS provider adapter (Africa's Talking-class) with delivery receipts and a sandbox mode
- Per-site daily message cap and a per-patient daily cap, enforced before dispatch
- Global kill switch that stops all SMS immediately without a deploy
- Cost recorded per message, with a running per-site total for the M12 reports
- Message length and encoding handling so no message silently splits into three billable parts

## Acceptance criteria

- [ ] Hitting a cap stops sends and raises an alert rather than failing silently
- [ ] The kill switch stops all SMS within one minute, without a deploy
- [ ] Delivery receipts update the notification log to a terminal status
- [ ] Every message's cost is recorded and reportable per site
- [ ] Messages stay within one SMS segment, verified for every template
- [ ] Sandbox mode allows full local development with no spend

## Files touched

- `app/services/transports/sms.py`
- `app/services/notification_budget.py`
- `tests/integration/test_sms_caps.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/09-pricing-and-budget.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #65
