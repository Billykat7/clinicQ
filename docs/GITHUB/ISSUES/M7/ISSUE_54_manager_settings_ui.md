# Issue 54: Clinic manager settings UI (profile, hours, display mode, staff, services)

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 24, 27, 28
**Estimate:** 3 days
**Status:** Planned

## Context

Everything a clinic manager needs to run their own site without contacting the team: profile, hours,
queues, services, staff and the display mode. The display-mode control carries an explicit plain-language
warning, because it is the one setting that can put personal information on a public screen.

## Scope

- Clinic profile editor: name, sector, address and location (with a map picker), contact details
- Opening hours, holidays and one-tap temporary closure
- Queue and service management: add, rename, reorder, deactivate
- Staff list with invitations, role changes, room assignments and deactivation
- Display and privacy settings with a live preview of what the board will show

## Acceptance criteria

- [ ] A manager can change hours, queues and staff without developer involvement
- [ ] The map picker sets a coordinate that discovery immediately reflects
- [ ] The display-mode control shows a live preview and a plain-language privacy warning
- [ ] Every settings change is audited
- [ ] Settings are strictly scoped to the manager's own site
- [ ] Destructive actions (deactivating a queue, removing staff) require confirmation

## Files touched

- `app/web/dashboard/settings.py`
- `app/templates/dashboard/settings_*.html`
- `tests/integration/test_manager_settings.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #54
