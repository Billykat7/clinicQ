# Issue 47: Queue OpenAPI contract, concurrency and state-machine tests

> **In short:** The queue engine's promises are written down as a contract and hammered by concurrency, property and rush-hour tests before anyone builds on them.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 7 (weeks 13–14), with E on the same issue |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Quality |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering<br>[Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection<br>[Issue 42](../M6/ISSUE_42_wait_time_estimation.md): Wait-time estimation service and `wait_time_samples`<br>[Issue 43](../M6/ISSUE_43_recall_noshow_timers.md): Recall timers and automatic no-show transitions (`arq` jobs)<br>[Issue 44](../M6/ISSUE_44_patient_cancel_recalculation.md): Patient cancellation and queue position recalculation<br>[Issue 45](../M6/ISSUE_45_queue_transfer.md): Transfer between queues without re-joining (triage → doctor → pharmacy)<br>[Issue 46](../M6/ISSUE_46_priority_override_audit.md): Clinical priority override with reason codes and audit trail |
| **Unblocks** | [Issue 105](../M14/ISSUE_105_load_soak_testing.md): Load and soak testing of the morning-rush profile |

> **Note:** E shares this in sprint 7 (concurrency and load tests); A writes the contract.

## Context

The queue engine is consumed by the dashboard, the board, the PWA and both channel adapters, and it is
the piece where a concurrency bug is most expensive. This issue freezes the contract and proves the
invariants under load rather than by inspection.

## Starting point

- Reuse the contract drift test from Issue 30.
- `hypothesis` is already in `requirements.txt` for the property tests, and `pytest-xdist` for running the suite in parallel.

## Scope

- Hand-written `contracts/queue.yaml` covering every ticket and queue route, including 409 lifecycle errors
- Drift test asserting the contract and the router agree in both directions
- Concurrency suite: parallel joins, simultaneous call-next on one queue, transfer during call
- Property-based tests over the state machine asserting no illegal state is reachable
- A load scenario simulating a 07:30 rush of 200 joins in 10 minutes across 4 queues

## Out of scope

- The production-scale load test (Issue 105).

## Acceptance criteria

- [ ] `queue.yaml` documents every route and every documented error status
- [ ] The concurrency suite passes repeatedly with no flakes across 20 consecutive runs
- [ ] Property tests find no reachable illegal state
- [ ] Two staff calling next simultaneously produce two different tickets, never the same one
- [ ] The rush scenario completes within the performance budget
- [ ] The whole suite runs inside the CI time budget

## How to verify

1. Run the concurrency suite 20 times in a row (a shell `for` loop around `pytest`): no flakes.
2. Two simulated staff press Call next at once: two different tickets.
3. The 07:30 rush scenario (200 joins in 10 minutes over 4 queues) meets the budget, reported in the PR.

## Files touched

- `contracts/queue.yaml`
- `tests/integration/queue/test_queue_concurrency.py`
- `tests/unit/queue/test_ticket_states_property.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #47
