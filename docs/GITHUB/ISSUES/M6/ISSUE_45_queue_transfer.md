# Issue 45: Transfer between queues without re-joining (triage → doctor → pharmacy)

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issues 41, 25
**Estimate:** 2 days
**Status:** Planned

## Context

A visit is a journey, not a single line. Moving a patient from triage to the doctor without making
them rejoin at the back is the difference between a system a nurse uses and one they route around.

## Scope

- `transfer_ticket(ticket, target_queue, staff, reason)` preserving a visit identifier across queues
- A `visit` record linking the tickets that belong to one patient journey
- The transferred ticket takes a new number in the target queue but keeps its visit history
- Configurable placement in the target queue: back of the line, or preserving arrival order
- Patient notified of the move and their new position

## Acceptance criteria

- [ ] A transferred patient appears in the target queue without rejoining
- [ ] The visit record links every leg of the journey and total visit time is derivable
- [ ] The transfer is audited with the staff member and reason
- [ ] The patient is notified of the new queue, number and estimate
- [ ] A transfer to an inactive or full queue is rejected with a clear error
- [ ] End-to-end triage → doctor → pharmacy is covered by an integration test

## Files touched

- `app/services/queue_transfer.py`
- `app/database/models/visit.py`
- `tests/integration/test_transfer.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #45
