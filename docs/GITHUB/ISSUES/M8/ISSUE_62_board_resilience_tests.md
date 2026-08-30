# Issue 62: Board resilience: cached last-known state, stale banner, recovery tests

**Area:** Frontend / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 57, 61
**Estimate:** 2 days
**Status:** Planned

## Context

Load-shedding and clinic Wi-Fi mean outages are routine, not exceptional. The board's required
behaviour is to keep showing the last known state, say plainly how old it is, and recover by itself,
tested deliberately rather than discovered during a pilot.

## Scope

- Last-known board state cached in the browser and re-rendered immediately on load
- Explicit stale banner naming the time of the last successful update
- Automatic recovery after network loss, server restart or power cut, with no human action
- Service worker so the board shell loads even with no network at boot
- A resilience test suite simulating network loss, server restart and slow links

## Acceptance criteria

- [ ] An offline board shows the last known state plus a clear stale banner with a timestamp
- [ ] The board recovers automatically within 30 seconds of the network returning
- [ ] A power cut and reboot returns to a working board with no human action
- [ ] The shell loads from cache when the box boots with no network
- [ ] The resilience suite covers offline, server restart and a slow-link profile
- [ ] An 8-hour soak run shows no memory growth or visual drift

## Files touched

- `app/static/js/board-offline.js`
- `app/static/board-sw.js`
- `tests/e2e/test_board_resilience.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #62
