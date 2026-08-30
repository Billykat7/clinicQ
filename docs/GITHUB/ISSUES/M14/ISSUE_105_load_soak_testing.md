# Issue 105: Load and soak testing of the morning-rush profile

**Area:** QA / Performance
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 47, 102
**Estimate:** 3 days
**Status:** Planned

## Context

Clinic load is not uniform: it is a wall of arrivals between 07:00 and 09:00 and then a long tail. The
test profile models that shape rather than a flat average, because the flat average is exactly the load
that never happens.

## Scope

- Load profile modelling a 07:30 rush: 200 joins in 10 minutes across 4 queues plus concurrent board and dashboard clients
- Soak test running a full simulated clinic day to expose leaks and connection exhaustion
- A written performance budget: join latency, board update latency, dashboard render, discovery search
- Bottleneck identification with fixes, and a re-run proving improvement
- Results documented with headroom against the pilot's expected load

## Acceptance criteria

- [ ] The rush profile completes within the performance budget with headroom
- [ ] An 8-hour soak shows no memory growth, connection leak or degradation
- [ ] SSE connections for board and dashboard remain stable for the full day
- [ ] Every budget breach is either fixed or documented with a justification
- [ ] Results are reproducible from a committed script
- [ ] Sizing recommendations for the production stack follow from the results

## Files touched

- `tests/load/`
- `docs/OPS/PERFORMANCE_BUDGET.md`
- `scripts/load_test.py`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #105
