# Issue 32: Discovery list UI with public/private/all toggle and sector badges

**Area:** Frontend / Discovery
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issue 31
**Estimate:** 3 days
**Status:** Planned

## Context

The first screen a patient sees. It has to work on a cheap Android phone on a weak connection, so it
is server-rendered with htmx, shows the three things that actually drive the decision (distance, queue
length, open or closed), and never blocks on a map tile.

## Scope

- Mobile-first list of nearby clinics: name, sector badge, distance, live queue length, wait estimate, open/closed
- Public / Private / All toggle that re-renders the list via an htmx swap without a full page load
- Location permission prompt with a clear explanation and a graceful decline path
- Loading skeletons, an empty state ('no clinics within 10 km. Try a wider radius') and an error state
- Radius selector and sort options (nearest, shortest queue)

## Acceptance criteria

- [ ] The list renders usable content in under 2 seconds on a throttled 3G profile
- [ ] Toggling sector re-renders the list without a full page reload
- [ ] Declining location shows the area-search fallback rather than a dead end
- [ ] Empty and error states are designed, not default browser output
- [ ] Sector badges are distinguishable without relying on colour alone
- [ ] The page is fully usable with a keyboard and passes an automated accessibility check

## Files touched

- `app/web/discover/routes.py`
- `app/templates/discover/list.html`
- `app/templates/discover/_clinic_card.html`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #32
