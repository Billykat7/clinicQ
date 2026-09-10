# Issue 50: Call next, recall, mark done and no-show actions

> **In short:** One big, safe Call Next per queue, plus recall, done and no-show, with a double tap never calling two patients.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection<br>[Issue 49](../M7/ISSUE_49_front_desk_board_live.md): Front-desk board: all active queues, live via SSE with polling fallback |
| **Unblocks** | [Issue 55](../M7/ISSUE_55_dashboard_offline_tests.md): Reconnect/offline states and dashboard interaction tests |

## Context

Call Next is pressed hundreds of times a day by someone who is simultaneously talking to a patient.
It is therefore large, instantly responsive, and forgiving: the optimistic update rolls back visibly if
the server rejects it, rather than leaving the screen and the room disagreeing.

## Starting point

- Every button calls `transition_ticket()` (Issue 41) through the queue API; the page never changes status itself.
- Room permissions come from Issue 28's assignments.

## Scope

- Prominent Call Next per queue, with optimistic UI and visible rollback on failure
- Recall, mark done, mark no-show and undo-last-call actions
- Confirmation only where an action is destructive or hard to reverse
- Called-ticket panel showing who is currently in the room and for how long
- Guard against double submission from a double tap or a slow connection

## Out of scope

- Reordering (Issue 52).
- Automatic recall and no-show timers (Issue 43).

## Acceptance criteria

- [ ] Call Next updates the patient's phone and the waiting-room board within 2 seconds
- [ ] A double tap issues exactly one call, proven by a test
- [ ] A server rejection rolls the optimistic update back and explains why
- [ ] Undo-last-call is available for a short window and is audited
- [ ] A nurse can only call from a queue they are assigned to
- [ ] The in-room panel shows elapsed consultation time

## How to verify

1. Double-click Call next quickly: exactly one ticket is called.
2. Make the server reject a call (for example, a nurse on the wrong room): the card rolls back and says why.
3. Undo the last call within the window: the ticket is back and the audit log shows both steps.

## Files touched

- `src/web/dashboard/actions.py`
- `src/templates/dashboard/_queue_card.html`
- `tests/integration/dashboard/test_dashboard_actions.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #50
