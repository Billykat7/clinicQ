# Issue 48: Dashboard shell, role-aware navigation and site switcher

> **In short:** The frame every staff screen lives in: each role sees only the sections it can use, and staff who work at two clinics switch between them without signing out.

| | |
|---|---|
| **Milestone** | [M7: Clinic Dashboard](../../MILESTONES/M7_clinic_dashboard.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Dashboard |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 18](../M3/ISSUE_18_rbac_roles_enforcement.md): RBAC model, seeded roles and enforcement dependencies<br>[Issue 28](../M4/ISSUE_28_staff_site_room_assignment.md): Staff-to-site and room assignment |
| **Unblocks** | [Issue 49](../M7/ISSUE_49_front_desk_board_live.md): Front-desk board: all active queues, live via SSE with polling fallback<br>[Issue 53](../M7/ISSUE_53_nurse_room_view_visit_notes.md): Nurse/doctor room view and private visit notes<br>[Issue 89](../M12/ISSUE_89_clinic_reports_ui.md): Clinic reports UI: wait time, no-show, channel mix, heatmap |

## Context

The frame every staff screen hangs in. It resolves the signed-in role and the current site once, so
individual pages never re-derive permissions, and a nurse working two clinics can move between them
without signing out.

## Starting point

- The signed-in shell already exists (`src/templates/base.html` with the icon rail in `src/templates/partials/app_nav.html`), and navigation is already permission-driven through `src/core/nav_registry.py` and `nav_visibility.py`. Add dashboard destinations to the registry rather than writing role checks in templates.
- Build against fixtures from sprint 3; the real site assignments arrive with Issue 28.

## Scope

- Auth-gated dashboard layout with a persistent header, site name and signed-in identity
- Role-aware navigation rendering only the sections the role can reach
- Site switcher for staff assigned to more than one clinic
- Dense, information-first layout tuned for an old reception PC at 1366×768
- Keyboard shortcuts for the highest-frequency actions

## Out of scope

- The queue board content (Issue 49) and settings screens (Issue 54).

## Acceptance criteria

- [ ] A receptionist, nurse and clinic manager each see a different, correct navigation
- [ ] Switching sites reloads the board scoped to the new site without a re-authentication
- [ ] The layout is fully usable at 1366×768 without horizontal scrolling
- [ ] Navigation is driven by permissions data, not hard-coded role checks in templates
- [ ] The primary actions are reachable by keyboard
- [ ] An unauthenticated visit redirects to sign-in and returns to the intended page afterwards

## How to verify

1. Sign in as a receptionist, a nurse and a clinic manager: three different, correct menus.
2. Switch sites as a two-site staff member: the board reloads for the new site, no sign-in.
3. Open the dashboard at 1366×768: no horizontal scrolling.
4. Visit a dashboard URL signed out: sign in, then land back on that URL.

## Files touched

- `src/web/dashboard/routes.py`
- `src/core/nav_registry.py`
- `src/templates/dashboard/base.html`
- `src/templates/partials/app_nav.html`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #48
