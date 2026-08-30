# Issue 37: Payment and medical-aid directory filter (private clinics only)

**Area:** Backend / Discovery
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Data & Research Lead
**Depends on:** Issues 23, 32
**Estimate:** 2 days
**Status:** Planned

## Context

Sending a patient to a private clinic that will not take their scheme is worse than not filtering at
all. This is therefore explicitly a **self-reported directory attribute** with a visible disclaimer, not
an eligibility check, and it appears only when the sector filter is set to Private.

## Scope

- `site_payment_profile`: accepts cash, accepts card, accepted medical-aid tags, copay notice, last confirmed date
- Clinic-side editor for a private clinic to declare and re-confirm what it accepts
- Discovery filter shown only under the Private sector, with a multi-select of scheme tags
- A prominent 'reported by the clinic; please confirm before travelling' disclaimer wherever the data appears
- A staleness indicator when the profile has not been confirmed in over six months

## Acceptance criteria

- [ ] Payment filters are completely hidden when the Public sector is selected
- [ ] Medical-aid data is displayed with a clinic-reported disclaimer everywhere it appears
- [ ] Only private clinics can hold a payment profile, enforced server-side
- [ ] A profile not confirmed in six months is marked stale in the UI
- [ ] Scheme tags come from a controlled list with a free-text 'other' option
- [ ] The feature is behind a flag so it can ship after the MVP without a code change

## Files touched

- `app/database/models/site_payment_profile.py`
- `app/services/payment_profile.py`
- `app/templates/discover/_payment_filter.html`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #37
