# Issue 49: Front-desk board: all active queues, live via SSE with polling fallback

> **In short:** Reception's main screen: every active queue at a glance, updating live, and honest about it when the connection drops.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 6 (weeks 11–12) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards<br>[Issue 48](../M7/ISSUE_48_dashboard_shell_role_nav.md): Dashboard shell, role-aware navigation and site switcher |
| **Unblocks** | [Issue 50](../M7/ISSUE_50_call_next_actions.md): Call next, recall, mark done and no-show actions<br>[Issue 51](../M7/ISSUE_51_walkin_intake_ticket_stub.md): Walk-in intake form and printable ticket stub<br>[Issue 55](../M7/ISSUE_55_dashboard_offline_tests.md): Reconnect/offline states and dashboard interaction tests<br>[Issue 90](../M12/ISSUE_90_live_operational_kpis.md): Live operational KPIs on the manager dashboard |

## Context

The screen reception keeps open all day. Every active queue side by side, each card answering 'is
anyone stuck?' at a glance, updating live and, critically, telling the truth when the connection drops
instead of showing a frozen queue that looks current.

## Starting point

- Build against the queue contract (Issue 47) from sprint 6, before the engine is finished.
- There is no server-sent events (SSE) code in the kernel yet; Issue 57 builds the stream for the board, and this page can share its event format.
- Loading and error feedback can reuse `src/static/js/ui-feedback.js`.

## Scope

- Queue cards for every active queue: current length, average wait today, oldest waiting ticket's age
- SSE stream for live updates, with an automatic fall back to htmx polling when SSE is unavailable
- Visual escalation when a ticket has waited beyond a threshold
- Expand a queue card to the full ticket list with status and wait time per ticket
- Connection state indicator showing live, reconnecting, or the age of the displayed data

## Out of scope

- The buttons on each card (Issue 50) and walk-in intake (Issue 51).
- The full offline and retry behaviour (Issue 55).

## Acceptance criteria

- [ ] The board reflects a change made on another device within 2 seconds
- [ ] SSE failure falls back to polling automatically, without a page reload
- [ ] A stuck ticket is visually obvious without reading numbers
- [ ] Losing the network shows 'reconnecting, data from HH:MM', never a silently frozen board
- [ ] The board handles 10 queues and 200 waiting tickets without noticeable lag
- [ ] Updates do not steal focus from a form a receptionist is typing into

## How to verify

1. Call a ticket from a second browser: the first updates within 2 seconds.
2. Block the SSE endpoint: the page switches to polling without a reload.
3. Disconnect the network: "reconnecting, data from HH:MM" appears; the board never looks current when it is not.
4. Seed 10 queues and 200 tickets: scrolling and updates stay smooth.

## Files touched

- `src/web/dashboard/board.py`
- `src/templates/dashboard/board.html`
- `src/static/js/dashboard-live.js`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #49
