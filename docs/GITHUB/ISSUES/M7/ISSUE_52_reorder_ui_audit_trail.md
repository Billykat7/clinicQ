# Issue 52: Drag-to-reorder with reason codes and inline audit trail

> **In short:** Staff can move a patient up the queue by dragging or tapping, but only with a reason, and the trail of every move stays visible on the board.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 8 (weeks 15–16) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 46](../M6/ISSUE_46_priority_override_audit.md): Clinical priority override with reason codes and audit trail |
| **Unblocks** | No other issue waits on this one. |

## Context

The human-override valve, made visible. Staff get a two-tap bump with a reason prompt; the clinic
manager gets an inline trail showing who moved whom and why. Both halves are required: the ease is what
gets it used, the trail is what keeps it fair.

## Starting point

- The UI for Issue 46's priority service; the reason codes and audit rows already exist once that merges.
- Keep drag-and-drop in an external JS file (`src/static/js/`); CSP forbids inline handlers.

## Scope

- Drag-to-reorder within a queue, with a touch-friendly move-up control as an alternative
- Reason-code prompt on every reorder, with an optional note
- Inline audit trail on the queue card showing recent overrides
- Priority badge on reordered tickets, visible to staff only
- Manager view of all overrides for the day with counts per staff member

## Out of scope

- The priority rules themselves (Issue 46).
- Any public display of priority (never shown on the waiting-room board).

## Acceptance criteria

- [ ] A reorder cannot be completed without selecting a reason code
- [ ] Drag and the tap-alternative produce identical results
- [ ] The audit trail is visible without leaving the board
- [ ] Priority badges never appear on the public waiting-room board
- [ ] The manager view shows override counts per staff member for the day
- [ ] Reordering is disabled for roles without the permission

## How to verify

1. Drag a ticket up without choosing a reason: the move does not save.
2. Do the same move with the tap alternative: identical result.
3. Open the public board: no priority badge anywhere.

## Files touched

- `src/templates/dashboard/_reorder.html`
- `src/static/js/dashboard-reorder.js`
- `src/web/dashboard/actions.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #52
