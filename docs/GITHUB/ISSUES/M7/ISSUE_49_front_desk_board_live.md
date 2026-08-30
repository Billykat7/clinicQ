# Issue 49: Front-desk board: all active queues, live via SSE with polling fallback

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 40, 48
**Estimate:** 3 days
**Status:** Planned

## Context

The screen reception keeps open all day. Every active queue side by side, each card answering 'is
anyone stuck?' at a glance, updating live and, critically, telling the truth when the connection drops
instead of showing a frozen queue that looks current.

## Scope

- Queue cards for every active queue: current length, average wait today, oldest waiting ticket's age
- SSE stream for live updates, with an automatic fall back to htmx polling when SSE is unavailable
- Visual escalation when a ticket has waited beyond a threshold
- Expand a queue card to the full ticket list with status and wait time per ticket
- Connection state indicator showing live, reconnecting, or the age of the displayed data

## Acceptance criteria

- [ ] The board reflects a change made on another device within 2 seconds
- [ ] SSE failure falls back to polling automatically, without a page reload
- [ ] A stuck ticket is visually obvious without reading numbers
- [ ] Losing the network shows 'reconnecting, data from HH:MM', never a silently frozen board
- [ ] The board handles 10 queues and 200 waiting tickets without noticeable lag
- [ ] Updates do not steal focus from a form a receptionist is typing into

## Files touched

- `app/web/dashboard/board.py`
- `app/templates/dashboard/board.html`
- `app/static/js/live.js`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #49
