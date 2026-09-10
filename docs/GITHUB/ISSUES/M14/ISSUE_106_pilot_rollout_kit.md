# Issue 106: Pilot rollout kit: site survey, install guide, staff training pack

> **In short:** Everything needed to set up a real clinic without the developers on site: a survey checklist, an install guide, a training pack and a paper fallback.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Ops / Pilot |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 61](../M8/ISSUE_61_kiosk_device_registry.md): Kiosk device registry, pairing codes and heartbeat monitoring<br>[Issue 102](../M14/ISSUE_102_production_infra_tls.md): Production infrastructure, TLS, domains and edge protection |
| **Unblocks** | [Issue 107](../M14/ISSUE_107_support_incident_sla.md): Support process, incident runbooks and internal SLA<br>[Issue 108](../M14/ISSUE_108_uat_clinic_staff.md): User acceptance testing with clinic staff and remediation |

## Context

Everything a clinic needs to start using ClinicQ without a developer in the room. If the kit is not
good enough for someone else to follow, the product does not scale past the first site, which is exactly
what the upscaling plan depends on.

## Starting point

- Hardware choices and prices are in [`docs/PRODUCT/07-devices-and-bom.md`](../../../PRODUCT/07-devices-and-bom.md).
- The kiosk install guide extends Issue 61's `KIOSK_SETUP.md` rather than starting a second one.

## Scope

- Site survey checklist: network, screen position, power, reception device, printer
- Kiosk install guide with photographs, from unboxing to a running board
- Staff training pack: quick-reference card, a 10-minute walkthrough, and a short video
- Printed fallback procedure for when the internet is down
- Go-live checklist and a day-one support plan

## Out of scope

- Running UAT (Issue 108).

## Acceptance criteria

- [ ] Someone who did not write the guide has installed a kiosk using it alone
- [ ] The training pack gets a receptionist to competent walk-in intake within 15 minutes
- [ ] The quick-reference card fits on one laminated page
- [ ] The paper fallback procedure has been rehearsed with staff
- [ ] The site survey caught at least one real issue at the pilot clinic before install
- [ ] All materials are in the repository and versioned

## How to verify

1. Someone who did not write the install guide gets a board running using it alone.
2. Time a new receptionist through the training pack to competent walk-in intake: under 15 minutes.
3. Rehearse the paper fallback with staff, and record what went wrong.

## Files touched

- `docs/OPS/SITE_SURVEY.md`
- `docs/OPS/KIOSK_SETUP.md`
- `docs/TRAINING/`
- `docs/OPS/GO_LIVE_CHECKLIST.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/07-devices-and-bom.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #106
