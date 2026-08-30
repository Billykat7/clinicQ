# Milestone 13: Security, Privacy & POPIA Compliance

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 12–13 · **Suggested tag:** `v0.13.0`
**Primary owner:** DevOps/QA Lead · Data & Research Lead
**Depends on:** M3, M8, M9
**Blocks:** M14 go-live; a pilot with real patients cannot start before this closes

## Goal

Make the privacy promises in the design enforceable and provable: a data map with automatic retention purges, data-subject access and erasure, hardened endpoints, encrypted patient contact data, a tamper-evident audit trail, an authorised penetration test, and a WCAG 2.2 AA audit.

## Why this milestone exists

ClinicQ handles health-adjacent personal information about identifiable people: a phone number, a
reason for visit, a clinic attended, a time. Under POPIA that is special personal information the
moment a name is attached to it, and the project has said in writing (docs 04 and 13) that reason
text and visit notes are purged on a short retention window. **This milestone is where that sentence
becomes a scheduled job with a test**, rather than an intention.

The penetration test is scoped and authorised in writing against the team's own staging environment
only. Nothing in this milestone is a licence to test any third party's system.

## Scope

- Data map of every personal field, its purpose, lawful basis and retention period
- Automatic purge jobs for `reason_text`, `visit_notes` and stale patient records, with proof tests
- Data-subject rights: machine-readable access export and an erasure workflow with audit
- Hardening: rate limits, security headers, CSRF, dependency and secret scanning in CI
- TLS everywhere, encryption at rest, field-level encryption for patient phone numbers
- Tamper-evident audit log (hash-chained) plus an admin viewer UI
- Authorised penetration test of the team's own staging environment and remediation
- WCAG 2.2 AA audit across patient, dashboard and board surfaces, with remediation

## Issues

| # | Title |
|---|-------|
| 95 | Data map, retention policy and automatic purge jobs |
| 96 | Data-subject access export and erasure workflow |
| 97 | Hardening: rate limits, security headers, dependency and secret scanning |
| 98 | Encryption in transit and at rest, field-level encryption for patient contacts |
| 99 | Tamper-evident (hash-chained) audit log and admin viewer UI |
| 100 | Authorised penetration test of staging and remediation |
| 101 | WCAG 2.2 AA accessibility audit and remediation |

## Exit criteria

- [ ] Reason text and visit notes are provably gone after the retention window, checked by a test
- [ ] A patient can request everything held about them and receive it in a machine-readable file
- [ ] An erasure request removes or irreversibly anonymises personal data while preserving aggregate counts
- [ ] CI fails on a known-vulnerable dependency or a committed secret
- [ ] Patient phone numbers are unreadable in a raw database dump
- [ ] The audit chain detects any retrospective edit of a past entry
- [ ] All three surfaces pass an automated and a manual WCAG 2.2 AA check

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M13/)
