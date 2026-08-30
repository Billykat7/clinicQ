# Issue 96: Data-subject access export and erasure workflow

**Area:** Backend / Compliance
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** Data & Research Lead
**Depends on:** Issues 95, 17
**Estimate:** 3 days
**Status:** Planned

## Context

POPIA gives a data subject the right to know what is held about them and to have it deleted. Building
the workflow now, rather than promising it in a policy document, is both the honest position and a
genuinely differentiating piece of capstone work.

## Scope

- Verified request flow: a patient proves control of their phone number by OTP before any data is released
- Machine-readable export of everything held: tickets, visits, consents, notifications, feedback
- Erasure that removes or irreversibly anonymises personal data while preserving aggregate counts
- Staff-side request queue with SLA tracking and full audit
- Documented handling of data the clinic must retain for its own legal reasons

## Acceptance criteria

- [ ] A patient receives a complete machine-readable export after OTP verification
- [ ] Erasure leaves no recoverable personal identifier, proven by a test
- [ ] Aggregate statistics remain correct after an erasure
- [ ] Every request and its outcome is audited with timestamps against the SLA
- [ ] An unverified request releases nothing
- [ ] Legal-retention exceptions are documented and applied consistently

## Files touched

- `app/services/data_rights.py`
- `app/api/data_rights.py`
- `docs/COMPLIANCE/DSAR_PROCEDURE.md`
- `tests/integration/test_dsar.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #96
