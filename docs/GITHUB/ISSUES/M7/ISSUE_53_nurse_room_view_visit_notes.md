# Issue 53: Nurse/doctor room view and private visit notes

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 28, 48
**Estimate:** 2 days
**Status:** Planned

## Context

A nurse needs one queue, one button and somewhere to write a short note, not the whole clinic. The
note is explicitly staff-only and must never reach the public board; that separation is enforced on the
server, not by remembering which template to use.

## Scope

- Room view scoped to the signed-in nurse's assigned queues only
- Call next, mark done, transfer to another queue, and add a private visit note
- `visit_notes` stored staff-only, with the retention window from the M13 data map
- Previous-visit notes for the same patient at the same site, where consent allows
- A large-touch-target layout usable on a tablet in a consulting room

## Acceptance criteria

- [ ] A nurse sees only their assigned queues and cannot act on another room
- [ ] Visit notes are never included in any board or patient-facing response, proven by a test
- [ ] Notes are attributed to the author and timestamped
- [ ] Notes are purged on the retention schedule
- [ ] The view is comfortably usable on a 10-inch tablet
- [ ] Transfer from the room view preserves the visit record

## Files touched

- `app/web/dashboard/room.py`
- `app/database/models/visit_note.py`
- `app/templates/dashboard/room.html`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #53
