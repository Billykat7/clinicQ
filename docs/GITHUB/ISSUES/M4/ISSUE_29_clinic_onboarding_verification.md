# Issue 29: Clinic onboarding and platform-admin verification workflow

> **In short:** A clinic can sign itself up, and a platform admin checks it before it ever appears to patients.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 4 (weeks 7–8) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Clinics |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD<br>[Issue 24](../M4/ISSUE_24_opening_hours_closures.md): Opening hours, holiday calendar and temporary-closure broadcast |
| **Unblocks** | [Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests |

## Context

A discovery directory is only trustworthy if the entries are real. Anyone can submit a clinic; a
platform admin verifies it before it becomes publicly visible, which also gives the pilot a controlled
way to add sites without a developer running SQL.

## Starting point

- The public form belongs on the front door: follow `src/templates/web/privacy.html` for layout (`landing.css`, `lp_header` and `lp_footer`) and add the route in `src/web/routes.py`.
- The verification queue is an admin console; the kernel's consoles under `src/templates/admin/` show the pattern.

## Scope

- Public clinic-registration form capturing name, sector, address, hours, contact and a responsible person
- Verification queue for platform admins with approve, reject and request-more-information actions
- Site status lifecycle: `draft` → `pending_verification` → `verified` → `suspended`
- Only `verified` sites appear in discovery; the rest are reachable by direct link for testing
- Notification to the submitter at each state change

## Out of scope

- Payment and medical-aid details (Issue 37).
- Notifications infrastructure (Issue 63); this issue only emits the status-change events.

## Acceptance criteria

- [ ] An unverified clinic never appears in a discovery search, proven by a test
- [ ] A platform admin can approve, reject with a reason, or request more information
- [ ] Every status change is audited with the deciding admin
- [ ] The submitter is notified at each transition
- [ ] A suspended site stops accepting joins immediately
- [ ] The verification queue is reachable only by platform admins

## How to verify

1. Submit the public form: the site is `pending_verification` and absent from discovery.
2. Approve it as a platform admin: it appears in search, and the submitter is notified.
3. Suspend it: new joins stop immediately.

## Files touched

- `src/modules/sites/onboarding.py`
- `src/web/routes.py`
- `src/templates/web/register_clinic.html`
- `src/templates/admin/verification.html`
- `tests/integration/sites/test_onboarding.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #29
