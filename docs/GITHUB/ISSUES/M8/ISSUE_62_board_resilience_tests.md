# Issue 62: Board resilience: cached last-known state, stale banner, recovery tests

> **In short:** Load-shedding, a flaky router or a server restart never leaves a waiting room staring at a frozen or blank screen.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 10–11 (weeks 19–22), with C on the same issue |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Display |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 57](../M8/ISSUE_57_board_sse_channel.md): SSE live update channel with reconnect, backoff and heartbeat<br>[Issue 61](../M8/ISSUE_61_kiosk_device_registry.md): Kiosk device registry, pairing codes and heartbeat monitoring |
| **Unblocks** | No other issue waits on this one. |

> **Note:** C joins D on the offline tests in sprint 11.

## Context

Load-shedding and clinic Wi-Fi mean outages are routine, not exceptional. The board's required
behaviour is to keep showing the last known state, say plainly how old it is, and recover by itself,
tested deliberately rather than discovered during a pilot.

## Starting point

- The CSP already allows service workers from the app's own origin (`worker-src 'self'`).
- Browser test tooling arrives with Issue 55; reuse it rather than adding a second framework.

## Scope

- Last-known board state cached in the browser and re-rendered immediately on load
- Explicit stale banner naming the time of the last successful update
- Automatic recovery after network loss, server restart or power cut, with no human action
- Service worker so the board shell loads even with no network at boot
- A resilience test suite simulating network loss, server restart and slow links

## Out of scope

- The dashboard's offline behaviour (Issue 55).
- The UPS and hardware side of power cuts (Issue 106).

## Acceptance criteria

- [ ] An offline board shows the last known state plus a clear stale banner with a timestamp
- [ ] The board recovers automatically within 30 seconds of the network returning
- [ ] A power cut and reboot returns to a working board with no human action
- [ ] The shell loads from cache when the box boots with no network
- [ ] The resilience suite covers offline, server restart and a slow-link profile
- [ ] An 8-hour soak run shows no memory growth or visual drift

## How to verify

1. Take the board offline: it keeps the last state and shows "last updated HH:MM".
2. Bring the network back: live again within 30 seconds, nobody touching it.
3. Boot the box with no network: the board shell loads from cache.

## Files touched

- `src/static/js/board-offline.js`
- `src/static/board-sw.js`
- `tests/e2e/display/test_board_resilience.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #62
