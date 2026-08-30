# Issue 78: Channel simulators for credential-free local development

**Area:** Backend / Tooling
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 73, 75
**Estimate:** 2 days
**Status:** Planned

## Context

Gateway credentials are slow to obtain, cost money, and cannot be shared across six laptops. The
simulators mean the entire channel layer can be built and tested before a single provider account
exists, and they keep working as regression tools afterwards.

## Scope

- `scripts/simulate_ussd.py`: an interactive terminal USSD session against the local webhook
- `scripts/simulate_whatsapp.py`: send inbound message and button payloads to the local webhook
- Recorded fixtures of real gateway payload shapes for realistic tests
- A simulator mode flag so no outbound provider call is ever made locally
- Documentation showing a new team member a full join flow in under 5 minutes

## Acceptance criteria

- [ ] A full USSD join can be driven from a terminal with no gateway account
- [ ] The WhatsApp simulator reproduces button and list interactions faithfully
- [ ] Fixtures match documented provider payload shapes
- [ ] Simulator mode makes outbound provider calls impossible
- [ ] A teammate unfamiliar with the channel code completes a simulated join in under 5 minutes
- [ ] The simulators are used by the automated test suite, not only by hand

## Files touched

- `scripts/simulate_ussd.py`
- `scripts/simulate_whatsapp.py`
- `tests/fixtures/gateway_payloads/`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #78
