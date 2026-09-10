# Issue 53: Nurse/doctor room view and private visit notes

> **In short:** A consulting-room view for nurses and doctors: only their own rooms, big touch targets, and private visit notes that never reach a public screen.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 8 (weeks 15–16) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 28](../M4/ISSUE_28_staff_site_room_assignment.md): Staff-to-site and room assignment<br>[Issue 48](../M7/ISSUE_48_dashboard_shell_role_nav.md): Dashboard shell, role-aware navigation and site switcher |
| **Unblocks** | [Issue 95](../M13/ISSUE_95_data_map_retention_purge.md): Data map, retention policy and automatic purge jobs |

## Context

A nurse needs one queue, one button and somewhere to write a short note, not the whole clinic. The
note is explicitly staff-only and must never reach the public board; that separation is enforced on the
server, not by remembering which template to use.

## Starting point

- Notes are health information: read [privacy non-negotiable 4](../../../guideline.md) and store them encrypted with the kernel's `EncryptedString` column type (`src/core/encryption.py`).
- Transfers use Issue 45's service.

## Scope

- Room view scoped to the signed-in nurse's assigned queues only
- Call next, mark done, transfer to another queue, and add a private visit note
- `visit_notes` stored staff-only, with the retention window from the M13 data map
- Previous-visit notes for the same patient at the same site, where consent allows
- A large-touch-target layout usable on a tablet in a consulting room

## Out of scope

- The retention purge (Issue 95), which deletes old notes.
- Any clinical record keeping beyond short visit notes.

## Acceptance criteria

- [ ] A nurse sees only their assigned queues and cannot act on another room
- [ ] Visit notes are never included in any board or patient-facing response, proven by a test
- [ ] Notes are attributed to the author and timestamped
- [ ] Notes are purged on the retention schedule
- [ ] The view is comfortably usable on a 10-inch tablet
- [ ] Transfer from the room view preserves the visit record

## How to verify

1. Sign in as a Room 2 nurse: Room 3 is not visible and its API calls are refused.
2. Write a note, then fetch the board and the patient's ticket page: the note appears in neither response.
3. Open the view on a 10-inch tablet (or the browser's tablet preset): comfortable to use.

## Files touched

- `src/web/dashboard/room.py`
- `src/database/models/visit_note.py`
- `src/templates/dashboard/room.html`
- `alembic/versions/NNNN_visit_notes.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #53
