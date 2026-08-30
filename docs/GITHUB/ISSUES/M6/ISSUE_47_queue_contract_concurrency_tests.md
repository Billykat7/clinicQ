# Issue 47: Queue OpenAPI contract, concurrency and state-machine tests

**Area:** Backend / Quality
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issues 39–46
**Estimate:** 3 days
**Status:** Planned

## Context

The queue engine is consumed by the dashboard, the board, the PWA and both channel adapters, and it is
the piece where a concurrency bug is most expensive. This issue freezes the contract and proves the
invariants under load rather than by inspection.

## Scope

- Hand-written `contracts/queue.yaml` covering every ticket and queue route, including 409 lifecycle errors
- Drift test asserting the contract and the router agree in both directions
- Concurrency suite: parallel joins, simultaneous call-next on one queue, transfer during call
- Property-based tests over the state machine asserting no illegal state is reachable
- A load scenario simulating a 07:30 rush of 200 joins in 10 minutes across 4 queues

## Acceptance criteria

- [ ] `queue.yaml` documents every route and every documented error status
- [ ] The concurrency suite passes repeatedly with no flakes across 20 consecutive runs
- [ ] Property tests find no reachable illegal state
- [ ] Two staff calling next simultaneously produce two different tickets, never the same one
- [ ] The rush scenario completes within the performance budget
- [ ] The whole suite runs inside the CI time budget

## Files touched

- `contracts/queue.yaml`
- `tests/integration/test_queue_concurrency.py`
- `tests/property/test_ticket_states.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #47
