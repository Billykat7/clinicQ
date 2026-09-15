# Issue 69: PWA shell: manifest, service worker, offline last-known ticket

> **In short:** Patients can install ClinicQ like an app, and still see their last known place in line when the network drops.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Patient |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 5](../M1/ISSUE_5_base_ui_shell_tailwind_htmx.md): Base UI shell: Jinja2 layout, Tailwind build, htmx + Alpine wiring<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel |
| **Unblocks** | [Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests |

## Context

Installable, launchable from the home screen, and useful with no signal, which matters because the
moment a patient most wants to check their position is often the moment they have the least connectivity,
on a taxi or inside a concrete waiting room.

## Starting point

- `src/templates/base.html` has no web app manifest yet. The CSP already allows service workers from the app's own origin.
- Keep this service worker separate from the board's (Issue 62).

## Scope

- `manifest.json` with icons, theme colour, standalone display and a start URL
- Service worker caching the shell and the last known ticket state
- Offline page showing the last known position with a clear 'last updated' timestamp
- Install prompt shown at a sensible moment, never on first load
- Update flow so a new version is picked up without the user clearing data

## Out of scope

- Push subscription (Issue 64).
- A native app ([backlog item 6](../BACKLOG/BACKLOG_06_native_app_offline_clinic.md)).

## Acceptance criteria

- [ ] The app installs to an Android home screen and launches standalone
- [ ] With no network, the last known ticket position is shown with its age
- [ ] A new deploy is picked up on the next launch without manual intervention
- [ ] The install prompt appears only after a successful join
- [ ] Lighthouse PWA checks pass
- [ ] The cache is bounded and cannot grow without limit

## How to verify

1. Install on Android: it opens standalone from the home screen.
2. Turn on airplane mode: the last known position shows with its age.
3. Deploy a change: the installed app picks it up on next launch.
4. Run Lighthouse's PWA checks: pass.

## Files touched

- `src/static/manifest.json`, `src/static/icons/` (from `scripts/render_app_icons.py`)
- `src/static/patient-sw.js` (the patient worker from Issue 64, extended; not a second `sw.js`)
- `src/templates/patient/offline.html`, `src/templates/patient/home.html`, `src/templates/components/pwa_head.html`
- `src/web/ticket.py` (`/t/`, `/t/offline`, the worker's version and shell)
- `src/static/js/patient-tickets.js`, `pwa.js`, `ticket-offline.js`, `patient-home.js`
- `docs/OPS/PATIENT_APP.md`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #69
