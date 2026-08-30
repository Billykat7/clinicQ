# Backlog 02: FHIR / EMR interoperability path

**Area:** Backend / Integrations · **Post-capstone** · **Depends on:** M6, M11

## Context

ClinicQ is deliberately not an EMR, no diagnosis coding, no dispensing, no lab results. But once a
clinic runs its queue here, the obvious next request is "can it talk to the system we already have?".
HL7 FHIR (the UK NHS and US Meaningful Use standard) is the route: expose `Appointment`, `Encounter`
and `Patient` resources read-only, and accept appointment creation from a practice-management system.

## Why it is parked

The benchmark doc is explicit that convergence with practice-management suites should be reached
**bottom-up**, after the discovery-and-queue core is proven across many clinics. Building FHIR
resources before there is a partner to consume them is speculative work.

## Rough scope when picked up

- Read-only FHIR endpoints for `Appointment`, `Encounter`, `Patient`, `Location`, `Schedule`, `Slot`
- SMART-on-FHIR-style authorisation with per-clinic scopes
- Inbound appointment creation from a practice-management system, mapped to ClinicQ slots
- Conformance statement and a published implementation guide
