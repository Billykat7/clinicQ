# Backlog 01: Verified medical-aid eligibility integration

**Area:** Backend / Integrations · **Post-capstone** · **Depends on:** Issue 37

## Context

M5 ships the medical-aid filter as a **self-reported directory attribute** with a "confirm with the
clinic" disclaimer. A real integration, checking a member's scheme and plan against a scheme switch at
the point of discovery, is a different project: every scheme has its own API, its own onboarding, and
its own contractual and compliance requirements. It only becomes worth the effort once enough private
clinics are live to make the integration commercially interesting.

## Why it is parked

Getting eligibility wrong sends a patient to a clinic that will not treat them on their plan: a worse
outcome than showing no filter at all. The honest self-reported tag captures most of the value at a
fraction of the cost and risk.

## Rough scope when picked up

- Scheme-by-scheme adapter interface with a shared eligibility result type
- Member consent and a verification flow that never stores scheme credentials
- Caching with a short TTL and a hard fallback to the self-reported tag
- Clear presentation of "verified" versus "clinic-reported" in discovery
