# Issue 55: Reconnect/offline states and dashboard interaction tests

> **In short:** The dashboard behaves well on a bad connection: it says when it is offline, retries safely, and never loses a receptionist's action without telling them.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 11 (weeks 21–22), with C on the same issue |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 49](../M7/ISSUE_49_front_desk_board_live.md): Front-desk board: all active queues, live via SSE with polling fallback<br>[Issue 50](../M7/ISSUE_50_call_next_actions.md): Call next, recall, mark done and no-show actions |
| **Unblocks** | No other issue waits on this one. |

> **Note:** C pairs with D on the offline tests in sprint 11.

## Context

Clinic internet drops, and load-shedding is a scheduled fact of life. The dashboard's job in that
moment is to be honest and to recover on its own; a frozen board that looks live is worse than an
obvious error, because staff will act on it.

## Starting point

- `src/static/js/session-refresh.js` already refreshes an expired access token and replays the request once, which covers part of the session-expiry criterion.
- There is no browser test tooling yet: adding Playwright to the dev dependencies and CI is part of this issue.

## Scope

- Explicit connection states: live, reconnecting with attempt count, offline with data age
- Queued actions retried on reconnect, with clear per-action feedback on success or failure
- Reconnection with exponential backoff and jitter, capped at a sensible interval
- Session-expiry handling that re-authenticates without losing in-progress work
- Playwright interaction tests covering the whole board including the offline paths

## Out of scope

- The waiting-room board's offline behaviour (Issue 62).

## Acceptance criteria

- [ ] Disconnecting the network shows the offline state within 10 seconds
- [ ] Reconnecting restores live updates without a manual refresh
- [ ] An action taken while offline either succeeds on reconnect or reports a clear failure, never silently vanishes
- [ ] An expired session prompts re-authentication and then completes the pending action
- [ ] Interaction tests cover call-next, walk-in, reorder and the offline path
- [ ] The suite runs in CI within the time budget

## How to verify

1. Switch the network off in dev tools: the offline state appears within 10 seconds.
2. Call next while offline, then reconnect: it either completes or shows a clear failure.
3. `pytest tests/e2e/dashboard` runs the Playwright suite locally and in CI inside the time budget.

## Files touched

- `src/static/js/dashboard-live.js`
- `tests/e2e/dashboard/test_dashboard.py`
- `requirements.txt`
- `docs/CICD/PIPELINES.md`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #55
