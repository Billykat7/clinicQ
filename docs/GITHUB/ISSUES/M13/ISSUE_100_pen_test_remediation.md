# Issue 100: Authorised penetration test of staging and remediation

> **In short:** An authorised, written-scope attack on staging finds what the team missed, and every serious finding is fixed before patients use the system.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 11–12 (weeks 21–24) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Security / QA |
| **Estimate** | 4 days |
| **Status** | Planned |
| **Depends on** | [Issue 97](../M13/ISSUE_97_hardening_rate_limits_scanning.md): Hardening: rate limits, security headers, dependency and secret scanning<br>[Issue 98](../M13/ISSUE_98_encryption_pii_protection.md): Encryption in transit and at rest, field-level encryption for patient contacts |
| **Unblocks** | [Issue 109](../M14/ISSUE_109_capstone_deliverables.md): Capstone deliverables: demo script, video, report, poster, presentation |

## Context

An authorised penetration test of **the team's own staging environment only**, conducted with written
scope and permission from the project supervisor. Nothing in this issue authorises testing any third
party's system, and the scope document is part of the deliverable.

## Starting point

- The kernel went through a penetration test in its source project (findings such as F-02 to F-04 are referenced in `src/core/rate_limit.py` and `src/core/otp_store.py`); read those notes before scoping.
- No testing starts until the scope document is signed and committed.

## Scope

- A written scope and authorisation document signed before testing begins
- Testing against staging only, with production and all third-party systems explicitly out of scope
- Coverage of the OWASP Top 10 plus the project's own risks: cross-tenant access, board privacy leakage, queue manipulation
- A findings report with severity ratings and reproduction steps
- Remediation of every high and medium finding, with a retest

## Out of scope

- Production and any third-party system (explicitly never in scope).

## Acceptance criteria

- [ ] The scope and authorisation document is signed and committed before any testing
- [ ] Only the team's own staging environment is tested
- [ ] Cross-tenant access, board privacy and queue-position manipulation are each explicitly tested
- [ ] Every high and medium finding is remediated and retested
- [ ] Accepted low-risk findings are documented with a rationale
- [ ] The report is suitable for inclusion as a capstone appendix

## How to verify

1. `docs/SECURITY/PENTEST_SCOPE.md` is signed and committed before the first test.
2. Each high and medium finding links to the PR that fixed it and a retest result.
3. Cross-tenant access, board privacy and queue manipulation each have their own section in the report.

## Files touched

- `docs/SECURITY/PENTEST_SCOPE.md`
- `docs/SECURITY/PENTEST_REPORT.md`
- `tests/security/`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #100
