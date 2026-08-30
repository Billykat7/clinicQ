# Issue 52: Drag-to-reorder with reason codes and inline audit trail

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issue 46
**Estimate:** 2 days
**Status:** Planned

## Context

The human-override valve, made visible. Staff get a two-tap bump with a reason prompt; the clinic
manager gets an inline trail showing who moved whom and why. Both halves are required: the ease is what
gets it used, the trail is what keeps it fair.

## Scope

- Drag-to-reorder within a queue, with a touch-friendly move-up control as an alternative
- Reason-code prompt on every reorder, with an optional note
- Inline audit trail on the queue card showing recent overrides
- Priority badge on reordered tickets, visible to staff only
- Manager view of all overrides for the day with counts per staff member

## Acceptance criteria

- [ ] A reorder cannot be completed without selecting a reason code
- [ ] Drag and the tap-alternative produce identical results
- [ ] The audit trail is visible without leaving the board
- [ ] Priority badges never appear on the public waiting-room board
- [ ] The manager view shows override counts per staff member for the day
- [ ] Reordering is disabled for roles without the permission

## Files touched

- `app/templates/dashboard/_reorder.html`
- `app/static/js/reorder.js`
- `app/web/dashboard/actions.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #52
