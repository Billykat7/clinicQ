# Issue 106: Pilot rollout kit: site survey, install guide, staff training pack

**Area:** Ops / Pilot
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** Data & Research Lead
**Depends on:** Issues 61, 102
**Estimate:** 3 days
**Status:** Planned

## Context

Everything a clinic needs to start using ClinicQ without a developer in the room. If the kit is not
good enough for someone else to follow, the product does not scale past the first site, which is exactly
what the upscaling plan depends on.

## Scope

- Site survey checklist: network, screen position, power, reception device, printer
- Kiosk install guide with photographs, from unboxing to a running board
- Staff training pack: quick-reference card, a 10-minute walkthrough, and a short video
- Printed fallback procedure for when the internet is down
- Go-live checklist and a day-one support plan

## Acceptance criteria

- [ ] Someone who did not write the guide has installed a kiosk using it alone
- [ ] The training pack gets a receptionist to competent walk-in intake within 15 minutes
- [ ] The quick-reference card fits on one laminated page
- [ ] The paper fallback procedure has been rehearsed with staff
- [ ] The site survey caught at least one real issue at the pilot clinic before install
- [ ] All materials are in the repository and versioned

## Files touched

- `docs/OPS/SITE_SURVEY.md`
- `docs/OPS/KIOSK_SETUP.md`
- `docs/TRAINING/`
- `docs/OPS/GO_LIVE_CHECKLIST.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/07-devices-and-bom.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #106
