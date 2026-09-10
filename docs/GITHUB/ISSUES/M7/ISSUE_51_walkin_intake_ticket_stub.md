# Issue 51: Walk-in intake form and printable ticket stub

> **In short:** A person walking up to the desk gets a ticket in under ten seconds, from the same queue as everyone who joined from home.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards<br>[Issue 49](../M7/ISSUE_49_front_desk_board_live.md): Front-desk board: all active queues, live via SSE with polling fallback |
| **Unblocks** | No other issue waits on this one. |

## Context

Most patients at a public clinic still arrive without booking. Intake has to be faster than writing a
name on a paper list, or reception will keep the paper list, which is why the target is under ten
seconds with an optional phone number and no required fields beyond a display name.

## Starting point

- Calls the join service from Issue 40 with `source=walk_in`; there is no separate walk-in path.
- The ticket stub is a print stylesheet on a plain page; test it on a 58 mm thermal printer if one is available, and the on-screen number otherwise.

## Scope

- Quick intake form: display name or initials, optional phone, optional reason, target queue
- Ticket issued into the same sequence as remote joins
- Printable ticket stub for a thermal printer, plus a large on-screen number to show the patient
- Optional SMS of the ticket number when a phone number is captured
- Recent-intake list with an undo for the last ticket issued

## Out of scope

- Kiosk self check-in (Issue 83).
- Notification delivery (M9); capturing the phone number is enough here.

## Acceptance criteria

- [ ] A walk-in ticket is issued in under 10 seconds of interaction
- [ ] The walk-in takes the next number in the shared sequence, not a separate one
- [ ] The stub prints correctly on a 58 mm thermal printer, and degrades to an on-screen number if none is attached
- [ ] Capturing a phone number triggers notifications for that ticket
- [ ] Undo is available for the most recent ticket and is audited
- [ ] The form is fully keyboard-operable with no mouse

## How to verify

1. Time a walk-in from first key press to the number on screen: under 10 seconds, mouse unused.
2. Issue a walk-in right after a remote join: consecutive numbers.
3. Undo the last walk-in: it is removed and the audit log records it.

## Files touched

- `src/web/dashboard/walkin.py`
- `src/templates/dashboard/walkin.html`
- `src/templates/print/ticket_stub.html`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #51
