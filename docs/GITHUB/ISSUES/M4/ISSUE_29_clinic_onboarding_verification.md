# Issue 29: Clinic onboarding and platform-admin verification workflow

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Data & Research Lead
**Depends on:** Issues 23, 24
**Estimate:** 2 days
**Status:** Planned

## Context

A discovery directory is only trustworthy if the entries are real. Anyone can submit a clinic; a
platform admin verifies it before it becomes publicly visible, which also gives the pilot a controlled
way to add sites without a developer running SQL.

## Scope

- Public clinic-registration form capturing name, sector, address, hours, contact and a responsible person
- Verification queue for platform admins with approve, reject and request-more-information actions
- Site status lifecycle: `draft` → `pending_verification` → `verified` → `suspended`
- Only `verified` sites appear in discovery; the rest are reachable by direct link for testing
- Notification to the submitter at each state change

## Acceptance criteria

- [ ] An unverified clinic never appears in a discovery search, proven by a test
- [ ] A platform admin can approve, reject with a reason, or request more information
- [ ] Every status change is audited with the deciding admin
- [ ] The submitter is notified at each transition
- [ ] A suspended site stops accepting joins immediately
- [ ] The verification queue is reachable only by platform admins

## Files touched

- `app/services/onboarding.py`
- `app/api/admin/verification.py`
- `app/templates/public/register_clinic.html`
- `tests/integration/test_onboarding.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #29
