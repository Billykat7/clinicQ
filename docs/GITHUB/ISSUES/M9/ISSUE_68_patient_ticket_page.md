# Issue 68: Patient ticket page: live position, ETA countdown, cancel

**Area:** Frontend / Patient
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issues 40, 42
**Estimate:** 3 days
**Status:** Planned

## Context

The screen a patient stares at while deciding whether to leave the house. It has one job: make the
answer to "how much longer?" unambiguous, honest and live, with the escape hatch (cancel) always
available.

## Scope

- Ticket page: number, clinic, queue, position, estimated wait range, live countdown
- Live updates via SSE with a polling fallback, and a visible last-updated time
- Prominent state changes for 'you are next' and 'please come in now'
- Cancel action with confirmation, plus directions and a tap-to-call clinic number
- A shareable ticket link, guessable by nobody, so a family member can follow along

## Acceptance criteria

- [ ] Position and estimate update live without a manual refresh
- [ ] 'You are next' is impossible to miss on a phone in a pocket-glance
- [ ] Cancelling takes two taps and confirms clearly
- [ ] The page works on a 320 px screen and on a throttled 3G profile
- [ ] The ticket link is a long unguessable token, not a sequential id
- [ ] The page shows how stale its data is whenever updates stop

## Files touched

- `app/web/queue/routes.py`
- `app/templates/queue/ticket.html`
- `app/static/js/ticket.js`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #68
