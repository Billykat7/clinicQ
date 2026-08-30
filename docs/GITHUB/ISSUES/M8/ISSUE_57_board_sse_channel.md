# Issue 57: SSE live update channel with reconnect, backoff and heartbeat

**Area:** Backend / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** Backend Lead
**Depends on:** Issues 41, 56
**Estimate:** 2 days
**Status:** Planned

## Context

The board must update the instant a nurse calls the next patient, and must recover on its own after a
power cut without anyone visiting the clinic. SSE over a long-lived connection with backoff reconnection
is the cheapest way to get both.

## Scope

- `GET /display/{site_id}/stream` SSE endpoint emitting board-state events per site
- Event types: ticket called, queue updated, board configuration changed, heartbeat
- Reconnection with exponential backoff and jitter, and a full state resync on reconnect
- Connection limits per site and clean-up of dead connections
- Heartbeat every 15 seconds so a silent connection is detectable within one interval

## Acceptance criteria

- [ ] A call-next reaches the board within 2 seconds
- [ ] Killing the connection triggers reconnection and a full resync
- [ ] A missed heartbeat is detected within 30 seconds and shown in the UI
- [ ] Dead connections are cleaned up and do not accumulate over an 8-hour day
- [ ] The stream is read-only and requires no authentication, exposing no personal data beyond the display mode
- [ ] A 10-minute outage recovers automatically with no manual intervention

## Files touched

- `app/web/display/stream.py`
- `app/services/board_state.py`
- `tests/integration/test_display_sse.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #57
