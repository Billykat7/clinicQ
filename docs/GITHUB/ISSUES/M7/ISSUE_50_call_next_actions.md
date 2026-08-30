# Issue 50: Call next, recall, mark done and no-show actions

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 41, 49
**Estimate:** 2 days
**Status:** Planned

## Context

Call Next is pressed hundreds of times a day by someone who is simultaneously talking to a patient.
It is therefore large, instantly responsive, and forgiving: the optimistic update rolls back visibly if
the server rejects it, rather than leaving the screen and the room disagreeing.

## Scope

- Prominent Call Next per queue, with optimistic UI and visible rollback on failure
- Recall, mark done, mark no-show and undo-last-call actions
- Confirmation only where an action is destructive or hard to reverse
- Called-ticket panel showing who is currently in the room and for how long
- Guard against double submission from a double tap or a slow connection

## Acceptance criteria

- [ ] Call Next updates the patient's phone and the waiting-room board within 2 seconds
- [ ] A double tap issues exactly one call, proven by a test
- [ ] A server rejection rolls the optimistic update back and explains why
- [ ] Undo-last-call is available for a short window and is audited
- [ ] A nurse can only call from a queue they are assigned to
- [ ] The in-room panel shows elapsed consultation time

## Files touched

- `app/web/dashboard/actions.py`
- `app/templates/dashboard/_queue_card.html`
- `tests/integration/test_dashboard_actions.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #50
