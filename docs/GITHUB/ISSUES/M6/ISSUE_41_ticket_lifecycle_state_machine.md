# Issue 41: Ticket lifecycle state machine and illegal-transition rejection

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issue 39
**Estimate:** 2 days
**Status:** Planned

## Context

Ticket status drives the board, the notifications, the reports and the patient's screen. An explicit
state machine that rejects illegal transitions keeps those four consumers consistent, and makes 'how did
this ticket get here?' a question the audit log can answer.

## Scope

- Explicit transition table: `waiting → called → in_progress → done`, plus `no_show`, `cancelled`, `transferred`
- A single `transition_ticket()` function; no route or template mutates status directly
- Illegal transitions rejected with 409 and no state change
- Every transition timestamped, attributed and audited
- Terminal states immutable, with corrections expressed as a new ticket rather than an edit

## Acceptance criteria

- [ ] Every illegal transition is rejected with 409 and leaves state untouched, covered exhaustively by tests
- [ ] No code path outside `transition_ticket()` writes `ticket.status`, enforced by a guard test
- [ ] Each transition writes an audit row with actor and timestamp
- [ ] A terminal ticket cannot be reopened
- [ ] The state diagram is documented and matches the transition table, verified by a test
- [ ] Concurrent transitions on one ticket are serialised, with the loser receiving 409

## Files touched

- `app/services/ticket_lifecycle.py`
- `app/core/enums.py`
- `docs/PRODUCT/03-booking-and-queue.md`
- `tests/unit/test_ticket_state_machine.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #41
