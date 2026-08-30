# Issue 84: Proxy booking for dependants with recorded consent

**Area:** Backend / Appointments
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Backend Lead
**Depends on:** Issues 17, 81
**Estimate:** 2 days
**Status:** Planned

## Context

A daughter books for her mother; a parent books for a child. Modelled after NHS App linked profiles,
this is one of the highest-value features in a market where one household often shares a single
smartphone, and it needs a clear consent and audit trail to stay defensible.

## Scope

- `patient_links`: proxy patient, dependant, relationship, consent record, active flag
- Booking and joining on behalf of a dependant, with the acting patient recorded on the ticket
- Notifications routed to the proxy's phone while the ticket belongs to the dependant
- Consent capture for acting on behalf, and a revocation path
- Board and dashboard clearly showing the dependant's details, not the proxy's

## Acceptance criteria

- [ ] A proxy can join a queue for a dependant, and the ticket belongs to the dependant
- [ ] Notifications reach the proxy's phone
- [ ] Every proxy action records who acted for whom, visible in the audit log
- [ ] Revoking the link stops further proxy actions immediately
- [ ] The board shows the dependant under the site's display rules, never the proxy
- [ ] A patient cannot link themselves to an arbitrary phone number without a verification step

## Files touched

- `app/database/models/patient_link.py`
- `app/services/proxy_booking.py`
- `tests/integration/test_proxy_booking.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #84
