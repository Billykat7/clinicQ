# Issue 43: Recall timers and automatic no-show transitions (`arq` jobs)

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issues 41, 2
**Estimate:** 2 days
**Status:** Planned

## Context

A called patient who is in the bathroom should not lose their place, and a patient who left an hour
ago should not hold the room up. Timed automatic transitions handle both, and the patient is always told
what happened and how to rejoin.

## Scope

- `arq` scheduled job checking called tickets against a per-site recall timeout
- First timeout moves the ticket to a recall state at the front of the active window; second marks it `no_show`
- Configurable timeout per site and per queue, with sensible defaults
- Patient notified on both recall and no-show, with rejoin instructions
- Manual staff override to recall or no-show immediately

## Acceptance criteria

- [ ] A called ticket not attended within the timeout auto-recalls exactly once
- [ ] A second timeout marks the ticket `no_show` and frees the room
- [ ] The patient is notified at both steps with a clear explanation
- [ ] Timers survive a worker restart and do not double-fire
- [ ] Staff can override either transition immediately from the dashboard
- [ ] Auto-transitions are audited as system actions, distinguishable from staff actions

## Files touched

- `workers/queue_timers.py`
- `app/services/ticket_lifecycle.py`
- `tests/integration/test_recall_timers.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #43
