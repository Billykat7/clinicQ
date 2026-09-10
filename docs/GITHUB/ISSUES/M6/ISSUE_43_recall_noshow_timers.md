# Issue 43: Recall timers and automatic no-show transitions (`arq` jobs)

> **In short:** A called patient who does not arrive is recalled once, then marked as a no-show, automatically and with a message saying why and how to rejoin.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 2](../M1/ISSUE_2_docker_dev_stack_postgis_redis.md): Docker Compose dev stack: PostgreSQL 18 + PostGIS, Redis, API<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests |

## Context

A called patient who is in the bathroom should not lose their place, and a patient who left an hour
ago should not hold the room up. Timed automatic transitions handle both, and the patient is always told
what happened and how to rejoin.

## Starting point

- The spec says `arq`; the kernel already runs scheduled jobs with APScheduler and a PostgreSQL advisory lock (`src/core/scheduler.py`), which already gives "survives a restart, never double-fires". Settle this before writing the job; see [open decisions](../README.md#open-decisions).
- Every automatic transition goes through `transition_ticket()` (Issue 41) with a system actor.

## Scope

- `arq` scheduled job checking called tickets against a per-site recall timeout
- First timeout moves the ticket to a recall state at the front of the active window; second marks it `no_show`
- Configurable timeout per site and per queue, with sensible defaults
- Patient notified on both recall and no-show, with rejoin instructions
- Manual staff override to recall or no-show immediately

## Out of scope

- Notification delivery (M9); until then, record the message in the notification ledger with the logging provider.
- The dashboard buttons for manual recall (Issue 50).

## Acceptance criteria

- [ ] A called ticket not attended within the timeout auto-recalls exactly once
- [ ] A second timeout marks the ticket `no_show` and frees the room
- [ ] The patient is notified at both steps with a clear explanation
- [ ] Timers survive a worker restart and do not double-fire
- [ ] Staff can override either transition immediately from the dashboard
- [ ] Auto-transitions are audited as system actions, distinguishable from staff actions

## How to verify

1. Call a ticket and advance the test clock past the timeout: recalled exactly once.
2. Advance again: `no_show`, the room is free, and the audit row shows a system actor.
3. Restart the app mid-timeout: the job neither skips nor fires twice.

## Files touched

- `src/modules/queue/timers.py`
- `src/core/scheduler.py`
- `src/modules/queue/lifecycle.py`
- `tests/integration/queue/test_recall_timers.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #43
