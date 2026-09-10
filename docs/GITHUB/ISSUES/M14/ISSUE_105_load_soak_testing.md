# Issue 105: Load and soak testing of the morning-rush profile

> **In short:** Proof that the 07:30 rush and a full clinic day will not break the system, with numbers that size the production servers.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | QA / Performance |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests<br>[Issue 102](../M14/ISSUE_102_production_infra_tls.md): Production infrastructure, TLS, domains and edge protection |
| **Unblocks** | [Issue 109](../M14/ISSUE_109_capstone_deliverables.md): Capstone deliverables: demo script, video, report, poster, presentation |

## Context

Clinic load is not uniform: it is a wall of arrivals between 07:00 and 09:00 and then a long tail. The
test profile models that shape rather than a flat average, because the flat average is exactly the load
that never happens.

## Starting point

- E drafts a load-test skeleton in sprint 6, and Issue 47's rush scenario is the starting point.
- No load-testing tool is in the repo yet; choose one that can hold long-lived SSE connections, which is where the risk is.

## Scope

- Load profile modelling a 07:30 rush: 200 joins in 10 minutes across 4 queues plus concurrent board and dashboard clients
- Soak test running a full simulated clinic day to expose leaks and connection exhaustion
- A written performance budget: join latency, board update latency, dashboard render, discovery search
- Bottleneck identification with fixes, and a re-run proving improvement
- Results documented with headroom against the pilot's expected load

## Out of scope

- Fixing application bugs found (each gets its own issue).

## Acceptance criteria

- [ ] The rush profile completes within the performance budget with headroom
- [ ] An 8-hour soak shows no memory growth, connection leak or degradation
- [ ] SSE connections for board and dashboard remain stable for the full day
- [ ] Every budget breach is either fixed or documented with a justification
- [ ] Results are reproducible from a committed script
- [ ] Sizing recommendations for the production stack follow from the results

## How to verify

1. `scripts/load_test.py` reproduces the rush profile, and the results sit inside `PERFORMANCE_BUDGET.md`.
2. An 8-hour soak shows flat memory and a stable SSE connection count.
3. The production sizing in Issue 102 cites these results.

## Files touched

- `scripts/load_test.py`
- `tests/load/`
- `docs/OPS/PERFORMANCE_BUDGET.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #105
