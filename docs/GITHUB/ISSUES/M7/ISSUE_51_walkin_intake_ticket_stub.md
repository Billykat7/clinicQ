# Issue 51: Walk-in intake form and printable ticket stub

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 40, 49
**Estimate:** 2 days
**Status:** Planned

## Context

Most patients at a public clinic still arrive without booking. Intake has to be faster than writing a
name on a paper list, or reception will keep the paper list, which is why the target is under ten
seconds with an optional phone number and no required fields beyond a display name.

## Scope

- Quick intake form: display name or initials, optional phone, optional reason, target queue
- Ticket issued into the same sequence as remote joins
- Printable ticket stub for a thermal printer, plus a large on-screen number to show the patient
- Optional SMS of the ticket number when a phone number is captured
- Recent-intake list with an undo for the last ticket issued

## Acceptance criteria

- [ ] A walk-in ticket is issued in under 10 seconds of interaction
- [ ] The walk-in takes the next number in the shared sequence, not a separate one
- [ ] The stub prints correctly on a 58 mm thermal printer, and degrades to an on-screen number if none is attached
- [ ] Capturing a phone number triggers notifications for that ticket
- [ ] Undo is available for the most recent ticket and is audited
- [ ] The form is fully keyboard-operable with no mouse

## Files touched

- `app/web/dashboard/walkin.py`
- `app/templates/dashboard/walkin.html`
- `app/templates/print/ticket_stub.html`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #51
