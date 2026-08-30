# Issue 48: Dashboard shell, role-aware navigation and site switcher

**Area:** Frontend / Dashboard
**Milestone:** M7 - Clinic Dashboard
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 18, 28
**Estimate:** 2 days
**Status:** Planned

## Context

The frame every staff screen hangs in. It resolves the signed-in role and the current site once, so
individual pages never re-derive permissions, and a nurse working two clinics can move between them
without signing out.

## Scope

- Auth-gated dashboard layout with a persistent header, site name and signed-in identity
- Role-aware navigation rendering only the sections the role can reach
- Site switcher for staff assigned to more than one clinic
- Dense, information-first layout tuned for an old reception PC at 1366×768
- Keyboard shortcuts for the highest-frequency actions

## Acceptance criteria

- [ ] A receptionist, nurse and clinic manager each see a different, correct navigation
- [ ] Switching sites reloads the board scoped to the new site without a re-authentication
- [ ] The layout is fully usable at 1366×768 without horizontal scrolling
- [ ] Navigation is driven by permissions data, not hard-coded role checks in templates
- [ ] The primary actions are reachable by keyboard
- [ ] An unauthenticated visit redirects to sign-in and returns to the intended page afterwards

## Files touched

- `app/web/dashboard/routes.py`
- `app/templates/dashboard/base.html`
- `app/templates/dashboard/_nav.html`

---

**Refs:** [M7 milestone](../../MILESTONES/M7_clinic_dashboard.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #48
