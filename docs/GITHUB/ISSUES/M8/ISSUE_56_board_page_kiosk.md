# Issue 56: Waiting-room board page (kiosk) with now-serving and up-next panels

> **In short:** The TV in the waiting room: who is being served now and who is next, readable from across the room, running all day on a cheap box.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Display |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 57](../M8/ISSUE_57_board_sse_channel.md): SSE live update channel with reconnect, backoff and heartbeat<br>[Issue 59](../M8/ISSUE_59_board_accessibility.md): Accessibility pass: contrast, type scale, 5-metre legibility, reduced motion<br>[Issue 61](../M8/ISSUE_61_kiosk_device_registry.md): Kiosk device registry, pairing codes and heartbeat monitoring |

> **Note:** The board may only ever receive the privacy projection from Issue 58, but Issue 58 is not in this issue's dependency list. Until it merges, build against a fixture of the projected shape, never raw tickets, and consider adding the dependency.

## Context

The most visible surface in the product: a screen a room full of people read from four metres away
while anxious about missing their turn. Everything about it (type size, contrast, the amount of
information, the newly-called highlight) follows from that single constraint.

## Starting point

- Build against fixtures from sprint 5, then switch to the real queue state.
- The board is a public page with no sign-in, so it uses the front-door conventions (no inline scripts or styles) but its own stylesheet and a huge type scale.
- Read [privacy non-negotiable 4](../../../guideline.md) first: this page only ever receives the projection from Issue 58, never raw tickets.

## Scope

- Board page: now serving per queue, plus the next 3–5 tickets, a clock and the clinic name
- Multi-queue layout that adapts from one panel to a four-panel grid based on active queue count
- Newly-called row highlighted briefly, with a reduced-motion alternative
- Very large type scale tuned for a 32-inch screen read at 4–5 metres
- Clinic branding slot and an optional health-message ticker

## Out of scope

- Live updates (Issue 57) and the privacy projection (Issue 58).
- Pairing a physical box to a clinic (Issue 61).

## Acceptance criteria

- [ ] Ticket numbers are legible at 5 metres on a 32-inch screen
- [ ] The layout adapts correctly for 1, 2, 3 and 4 or more active queues
- [ ] A newly called ticket is unmistakable without animation, for reduced-motion users
- [ ] The board runs for 8 hours in a browser without a memory leak or visual drift
- [ ] The page renders correctly at 1080p and 720p
- [ ] No scrollbars, cursor or browser chrome are visible in kiosk mode

## How to verify

1. Open the board with 1, 2, 3 and 5 active queues: the layout adapts each time.
2. Stand 5 metres from a 32-inch screen (or use the documented test chart): numbers are legible.
3. Leave it running for 8 hours with calls every few minutes: no memory growth in the browser's task manager.

## Files touched

- `src/web/display.py`
- `src/templates/display/board.html`
- `src/static/css/board.css`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #56
