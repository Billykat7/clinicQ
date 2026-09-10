# Issue 45: Transfer between queues without re-joining (triage → doctor → pharmacy)

> **In short:** A patient moves from triage to the doctor to the pharmacy without joining three separate lines, and the whole visit stays linked.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests |

## Context

A visit is a journey, not a single line. Moving a patient from triage to the doctor without making
them rejoin at the back is the difference between a system a nurse uses and one they route around.

## Starting point

- Greenfield: a `visit` record that the tickets of one journey share, plus a `transfer_ticket()` that uses Issue 41's `transferred` status.

## Scope

- `transfer_ticket(ticket, target_queue, staff, reason)` preserving a visit identifier across queues
- A `visit` record linking the tickets that belong to one patient journey
- The transferred ticket takes a new number in the target queue but keeps its visit history
- Configurable placement in the target queue: back of the line, or preserving arrival order
- Patient notified of the move and their new position

## Out of scope

- Staff notes on a visit (Issue 53).
- Reporting on total visit time (Issue 90 reads the visit).

## Acceptance criteria

- [ ] A transferred patient appears in the target queue without rejoining
- [ ] The visit record links every leg of the journey and total visit time is derivable
- [ ] The transfer is audited with the staff member and reason
- [ ] The patient is notified of the new queue, number and estimate
- [ ] A transfer to an inactive or full queue is rejected with a clear error
- [ ] End-to-end triage → doctor → pharmacy is covered by an integration test

## How to verify

1. Run triage → doctor → pharmacy in one integration test: three tickets, one visit, total time derivable.
2. Transfer into an inactive queue: refused with a clear message.
3. The patient's ticket page shows the new queue, number and estimate after each move.

## Files touched

- `src/modules/queue/transfer.py`
- `src/database/models/visit.py`
- `tests/integration/queue/test_transfer.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #45
