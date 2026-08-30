# Issue 69: PWA shell: manifest, service worker, offline last-known ticket

**Area:** Frontend / Patient
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issues 68, 5
**Estimate:** 2 days
**Status:** Planned

## Context

Installable, launchable from the home screen, and useful with no signal, which matters because the
moment a patient most wants to check their position is often the moment they have the least connectivity,
on a taxi or inside a concrete waiting room.

## Scope

- `manifest.json` with icons, theme colour, standalone display and a start URL
- Service worker caching the shell and the last known ticket state
- Offline page showing the last known position with a clear 'last updated' timestamp
- Install prompt shown at a sensible moment, never on first load
- Update flow so a new version is picked up without the user clearing data

## Acceptance criteria

- [ ] The app installs to an Android home screen and launches standalone
- [ ] With no network, the last known ticket position is shown with its age
- [ ] A new deploy is picked up on the next launch without manual intervention
- [ ] The install prompt appears only after a successful join
- [ ] Lighthouse PWA checks pass
- [ ] The cache is bounded and cannot grow without limit

## Files touched

- `app/static/manifest.json`
- `app/static/sw.js`
- `app/templates/patient/offline.html`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #69
