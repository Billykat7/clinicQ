# Issue 57: SSE live update channel with reconnect, backoff and heartbeat

> **In short:** When staff press Call next, every board at that clinic updates within two seconds and heals itself after a dropped connection.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 9 (weeks 17–18); the sprint plan puts this in **D**'s lane, see the note below |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Display |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection<br>[Issue 56](../M8/ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) with now-serving and up-next panels |
| **Unblocks** | [Issue 60](../M8/ISSUE_60_board_audio_tts.md): Audio chime and multi-language text-to-speech call announcements<br>[Issue 62](../M8/ISSUE_62_board_resilience_tests.md): Board resilience: cached last-known state, stale banner, recovery tests |

> **Note:** The spec names A as owner; the sprint plan puts the board SSE in D's lane (sprint 9). **Settled by the pull request for this issue:** A builds the stream (`src/web/display_stream.py`, `src/modules/display/board_state.py`), which carries the guard-tested privacy projection, as A owns Issue 58. D builds the client (`src/static/js/board-live.js`), which only draws what the stream brings.

## Context

The board must update the instant a nurse calls the next patient, and must recover on its own after a
power cut without anyone visiting the clinic. SSE over a long-lived connection with backoff reconnection
is the cheapest way to get both.

## Starting point

- No server-sent events (SSE) code exists in the kernel. FastAPI's `StreamingResponse` with the `text/event-stream` media type is enough; Redis pub/sub can fan events out across workers.
- The stream only ever carries the privacy projection from Issue 58.

## Scope

- `GET /display/{site_id}/stream` SSE endpoint emitting board-state events per site
- Event types: ticket called, queue updated, board configuration changed, heartbeat
- Reconnection with exponential backoff and jitter, and a full state resync on reconnect
- Connection limits per site and clean-up of dead connections
- Heartbeat every 15 seconds so a silent connection is detectable within one interval

## Out of scope

- The board page itself (Issue 56) and its offline cache (Issue 62).
- The dashboard's live updates (Issue 49), which can reuse this event format.

## Acceptance criteria

- [ ] A call-next reaches the board within 2 seconds
- [ ] Killing the connection triggers reconnection and a full resync
- [ ] A missed heartbeat is detected within 30 seconds and shown in the UI
- [ ] Dead connections are cleaned up and do not accumulate over an 8-hour day
- [ ] The stream is read-only and requires no authentication, exposing no personal data beyond the display mode
- [ ] A 10-minute outage recovers automatically with no manual intervention

## How to verify

1. Call next from the dashboard: the board updates within 2 seconds.
2. Kill the connection (restart the app): the board reconnects and resyncs on its own.
3. Watch the connection count over a simulated 8-hour day: it does not grow.

## Files touched

- `src/web/display_stream.py`
- `src/modules/display/board_state.py`
- `tests/integration/display/test_display_sse.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #57
