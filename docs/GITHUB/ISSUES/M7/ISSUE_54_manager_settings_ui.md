# Issue 54: Clinic manager settings UI (profile, hours, display mode, staff, services)

> **In short:** A clinic manager can run their own clinic's setup (profile, hours, queues, services, staff, display mode) without calling a developer.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 9 (weeks 17–18) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 24](../M4/ISSUE_24_opening_hours_closures.md): Opening hours, holiday calendar and temporary-closure broadcast<br>[Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site<br>[Issue 28](../M4/ISSUE_28_staff_site_room_assignment.md): Staff-to-site and room assignment |
| **Unblocks** | No other issue waits on this one. |

## Context

Everything a clinic manager needs to run their own site without contacting the team: profile, hours,
queues, services, staff and the display mode. The display-mode control carries an explicit plain-language
warning, because it is the one setting that can put personal information on a public screen.

## Starting point

- Mostly screens over services that already exist by then: hours (Issue 24), queues (25), services (26), display settings (27), assignments (28) and invitations (22).
- The map picker can reuse Leaflet from `src/static/vendor/leaflet/`.

## Scope

- Clinic profile editor: name, sector, address and location (with a map picker), contact details
- Opening hours, holidays and one-tap temporary closure
- Queue and service management: add, rename, reorder, deactivate
- Staff list with invitations, role changes, room assignments and deactivation
- Display and privacy settings with a live preview of what the board will show

## Out of scope

- Platform-admin verification of clinics (Issue 29).
- Payment profiles (Issue 37).

## Acceptance criteria

- [ ] A manager can change hours, queues and staff without developer involvement
- [ ] The map picker sets a coordinate that discovery immediately reflects
- [ ] The display-mode control shows a live preview and a plain-language privacy warning
- [ ] Every settings change is audited
- [ ] Settings are strictly scoped to the manager's own site
- [ ] Destructive actions (deactivating a queue, removing staff) require confirmation

## How to verify

1. As a manager, change the hours, add a queue and invite a receptionist: no developer involved, each change audited.
2. Move the clinic's pin on the map picker: discovery shows the new location.
3. Try to open another clinic's settings by URL: 404.

## Files touched

- `src/web/dashboard/settings.py`
- `src/templates/dashboard/settings_profile.html`
- `src/templates/dashboard/settings_hours.html`
- `src/templates/dashboard/settings_staff.html`
- `tests/integration/dashboard/test_manager_settings.py`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #54
