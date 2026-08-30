# Issue 56: Waiting-room board page (kiosk) with now-serving and up-next panels

**Area:** Frontend / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 41, 27
**Estimate:** 3 days
**Status:** Planned

## Context

The most visible surface in the product: a screen a room full of people read from four metres away
while anxious about missing their turn. Everything about it (type size, contrast, the amount of
information, the newly-called highlight) follows from that single constraint.

## Scope

- Board page: now serving per queue, plus the next 3–5 tickets, a clock and the clinic name
- Multi-queue layout that adapts from one panel to a four-panel grid based on active queue count
- Newly-called row highlighted briefly, with a reduced-motion alternative
- Very large type scale tuned for a 32-inch screen read at 4–5 metres
- Clinic branding slot and an optional health-message ticker

## Acceptance criteria

- [ ] Ticket numbers are legible at 5 metres on a 32-inch screen
- [ ] The layout adapts correctly for 1, 2, 3 and 4 or more active queues
- [ ] A newly called ticket is unmistakable without animation, for reduced-motion users
- [ ] The board runs for 8 hours in a browser without a memory leak or visual drift
- [ ] The page renders correctly at 1080p and 720p
- [ ] No scrollbars, cursor or browser chrome are visible in kiosk mode

## Files touched

- `app/web/display/routes.py`
- `app/templates/display/board.html`
- `app/static/css/board.css`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #56
