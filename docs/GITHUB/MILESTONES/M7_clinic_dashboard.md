# Milestone 7: Clinic Dashboard

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 8–9 · **Suggested tag:** `v0.7.0`
**Primary owner:** Frontend (Clinic) Dev
**Depends on:** M6 (M3 for roles)
**Blocks:** M14 pilot; staff cannot run a clinic without it

## Goal

Give reception, nurses and the clinic manager the one screen they run the day from: every active queue live, a big Call Next, walk-in intake, audited reordering, a private per-room view, and the settings that control the public board.

## Why this milestone exists

The dashboard is where the product either earns its place at the front desk or gets abandoned for the
paper list. Three design commitments come from that:

- **The primary action is enormous and always visible.** Call Next is used hundreds of times a day by
  someone who is also talking to a patient.
- **The board never lies about being live.** When the connection drops, the UI says so explicitly and
  shows the age of the data, rather than displaying a frozen queue that looks current.
- **Overrides are easy but recorded.** Staff must be able to bump a visibly unwell patient in two
  taps, and every bump is attributable, which is what keeps the "one fair queue" rule credible.

## Scope

- Auth-gated dashboard shell with role-aware navigation and site switching for multi-site staff
- Front-desk board: every active queue side by side, live via SSE with an htmx polling fallback
- Call next / recall / mark done / mark no-show actions with optimistic UI and rollback
- Walk-in intake: display name or initials, optional phone for notifications, optional reason, ticket stub
- Drag-to-reorder with a reason-code prompt and an inline audit trail
- Nurse/doctor room view limited to their own queue, with private visit notes
- Clinic manager settings UI: profile, hours, display mode, staff, services
- Reconnection and offline states, plus dashboard interaction tests

## Issues

| # | Title |
|---|-------|
| 48 | Dashboard shell, role-aware navigation and site switcher |
| 49 | Front-desk board: all active queues, live via SSE with polling fallback |
| 50 | Call next, recall, mark done and no-show actions |
| 51 | Walk-in intake form and printable ticket stub |
| 52 | Drag-to-reorder with reason codes and inline audit trail |
| 53 | Nurse/doctor room view and private visit notes |
| 54 | Clinic manager settings UI (profile, hours, display mode, staff, services) |
| 55 | Reconnect/offline states and dashboard interaction tests |

## Exit criteria

- [ ] A receptionist can issue a walk-in ticket in under 10 seconds without using a keyboard shortcut
- [ ] Call Next updates the patient's phone and the waiting-room board within 2 seconds
- [ ] A nurse signed into Room 2 sees only Room 2's queue and cannot call another room's ticket
- [ ] Losing the network shows an explicit 'reconnecting, data from HH:MM' banner, never a frozen board
- [ ] Private visit notes are never rendered on any public surface, proven by a test
- [ ] Every reorder shows who did it and why, without leaving the board

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M7/)
