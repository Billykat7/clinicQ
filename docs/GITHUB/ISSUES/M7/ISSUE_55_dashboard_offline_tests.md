# Issue 55: Reconnect/offline states and dashboard interaction tests

**Area:** Frontend / Quality
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 49, 50
**Estimate:** 2 days
**Status:** Planned

## Context

Clinic internet drops, and load-shedding is a scheduled fact of life. The dashboard's job in that
moment is to be honest and to recover on its own; a frozen board that looks live is worse than an
obvious error, because staff will act on it.

## Scope

- Explicit connection states: live, reconnecting with attempt count, offline with data age
- Queued actions retried on reconnect, with clear per-action feedback on success or failure
- Reconnection with exponential backoff and jitter, capped at a sensible interval
- Session-expiry handling that re-authenticates without losing in-progress work
- Playwright interaction tests covering the whole board including the offline paths

## Acceptance criteria

- [ ] Disconnecting the network shows the offline state within 10 seconds
- [ ] Reconnecting restores live updates without a manual refresh
- [ ] An action taken while offline either succeeds on reconnect or reports a clear failure, never silently vanishes
- [ ] An expired session prompts re-authentication and then completes the pending action
- [ ] Interaction tests cover call-next, walk-in, reorder and the offline path
- [ ] The suite runs in CI within the time budget

## Files touched

- `app/static/js/live.js`
- `tests/e2e/test_dashboard.py`
- `docs/CICD/PIPELINES.md`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #55
