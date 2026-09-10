# Issue 37: Payment and medical-aid directory filter (private clinics only)

> **In short:** Under "Private" only, patients can filter by cash, card or medical aid, always with a "reported by the clinic, please confirm" warning.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Discovery |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD<br>[Issue 32](../M5/ISSUE_32_discovery_list_ui_sector_toggle.md): Discovery list UI with public/private/all toggle and sector badges |
| **Unblocks** | [Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract |

## Context

Sending a patient to a private clinic that will not take their scheme is worse than not filtering at
all. This is therefore explicitly a **self-reported directory attribute** with a visible disclaimer, not
an eligibility check, and it appears only when the sector filter is set to Private.

## Starting point

- Owned by F, with the discovery filter UI on C (the sprint plan's "UI on 37").
- Ship it behind a feature flag in `src/core/config.py`; it is explicitly allowed to land after the MVP.
- [Backlog item 1](../BACKLOG/BACKLOG_01_medical_aid_integration.md) is the real claims integration; this issue is only a self-reported directory tag.

## Scope

- `site_payment_profile`: accepts cash, accepts card, accepted medical-aid tags, copay notice, last confirmed date
- Clinic-side editor for a private clinic to declare and re-confirm what it accepts
- Discovery filter shown only under the Private sector, with a multi-select of scheme tags
- A prominent 'reported by the clinic; please confirm before travelling' disclaimer wherever the data appears
- A staleness indicator when the profile has not been confirmed in over six months

## Out of scope

- Any claims, eligibility or billing check (backlog).
- Public clinics, which never carry a payment profile.

## Acceptance criteria

- [ ] Payment filters are completely hidden when the Public sector is selected
- [ ] Medical-aid data is displayed with a clinic-reported disclaimer everywhere it appears
- [ ] Only private clinics can hold a payment profile, enforced server-side
- [ ] A profile not confirmed in six months is marked stale in the UI
- [ ] Scheme tags come from a controlled list with a free-text 'other' option
- [ ] The feature is behind a flag so it can ship after the MVP without a code change

## How to verify

1. Select Public: the payment filter is not in the page at all.
2. Try to save a payment profile for a public clinic through the API: refused.
3. Set a profile's confirmation date seven months ago: it shows as stale.

## Files touched

- `src/modules/sites/payment_profile.py`
- `src/database/models/site_payment_profile.py`
- `src/templates/discover/_payment_filter.html`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #37
