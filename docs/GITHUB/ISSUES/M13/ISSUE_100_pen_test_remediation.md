# Issue 100: Authorised penetration test of staging and remediation

**Area:** Security / QA
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 97, 98
**Estimate:** 4 days
**Status:** Planned

## Context

An authorised penetration test of **the team's own staging environment only**, conducted with written
scope and permission from the project supervisor. Nothing in this issue authorises testing any third
party's system, and the scope document is part of the deliverable.

## Scope

- A written scope and authorisation document signed before testing begins
- Testing against staging only, with production and all third-party systems explicitly out of scope
- Coverage of the OWASP Top 10 plus the project's own risks: cross-tenant access, board privacy leakage, queue manipulation
- A findings report with severity ratings and reproduction steps
- Remediation of every high and medium finding, with a retest

## Acceptance criteria

- [ ] The scope and authorisation document is signed and committed before any testing
- [ ] Only the team's own staging environment is tested
- [ ] Cross-tenant access, board privacy and queue-position manipulation are each explicitly tested
- [ ] Every high and medium finding is remediated and retested
- [ ] Accepted low-risk findings are documented with a rationale
- [ ] The report is suitable for inclusion as a capstone appendix

## Files touched

- `docs/SECURITY/PENTEST_SCOPE.md`
- `docs/SECURITY/PENTEST_REPORT.md`
- `tests/security/`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #100
