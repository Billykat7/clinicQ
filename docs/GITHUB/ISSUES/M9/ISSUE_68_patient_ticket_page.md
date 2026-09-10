# Issue 68: Patient ticket page: live position, ETA countdown, cancel

> **In short:** The patient's own screen: their number, place in line and wait range, updating live, with a cancel button and a link family can follow.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Patient |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards<br>[Issue 42](../M6/ISSUE_42_wait_time_estimation.md): Wait-time estimation service and `wait_time_samples` |
| **Unblocks** | [Issue 69](../M9/ISSUE_69_pwa_shell_service_worker.md): PWA shell: manifest, service worker, offline last-known ticket<br>[Issue 70](../M9/ISSUE_70_qr_ticket_code.md): QR ticket code for kiosk check-in and reception lookup<br>[Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests<br>[Issue 86](../M11/ISSUE_86_virtual_waiting_room.md): Virtual waiting room and travel-time-aware call-forward<br>[Issue 101](../M13/ISSUE_101_accessibility_audit_remediation.md): WCAG 2.2 AA accessibility audit and remediation |

## Context

The screen a patient stares at while deciding whether to leave the house. It has one job: make the
answer to "how much longer?" unambiguous, honest and live, with the escape hatch (cancel) always
available.

## Starting point

- A public page (no account): route in `src/web/ticket.py`, templates in `src/templates/queue/`.
- Live updates reuse the SSE stream format from Issue 57; until it lands, poll.
- The landing page's patient phone mock-up (`src/templates/web/index.html`) is the agreed look.

## Scope

- Ticket page: number, clinic, queue, position, estimated wait range, live countdown
- Live updates via SSE with a polling fallback, and a visible last-updated time
- Prominent state changes for 'you are next' and 'please come in now'
- Cancel action with confirmation, plus directions and a tap-to-call clinic number
- A shareable ticket link, guessable by nobody, so a family member can follow along

## Out of scope

- Offline behaviour and installing the app (Issue 69).
- The QR code (Issue 70).

## Acceptance criteria

- [ ] Position and estimate update live without a manual refresh
- [ ] 'You are next' is impossible to miss on a phone in a pocket-glance
- [ ] Cancelling takes two taps and confirms clearly
- [ ] The page works on a 320 px screen and on a throttled 3G profile
- [ ] The ticket link is a long unguessable token, not a sequential id
- [ ] The page shows how stale its data is whenever updates stop

## How to verify

1. Leave the page open while tickets ahead are called: the position drops without a refresh.
2. Cancel: two taps and a clear confirmation.
3. Open the page at 320 px on Slow 3G: usable, and the "last updated" time is visible.

## Files touched

- `src/web/ticket.py`
- `src/templates/queue/ticket.html`
- `src/static/js/ticket.js`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #68
