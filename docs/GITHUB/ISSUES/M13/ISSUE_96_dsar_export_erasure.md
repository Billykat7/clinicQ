# Issue 96: Data-subject access export and erasure workflow

> **In short:** A patient can ask what ClinicQ holds about them and get it, or have it erased, after proving the phone number is theirs.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 13 (weeks 25–26); the sprint plan puts this in **A**'s lane, see the note below |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Compliance |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification<br>[Issue 95](../M13/ISSUE_95_data_map_retention_purge.md): Data map, retention policy and automatic purge jobs |
| **Unblocks** | No other issue waits on this one. |

## Context

POPIA gives a data subject the right to know what is held about them and to have it deleted. Building
the workflow now, rather than promising it in a policy document, is both the honest position and a
genuinely differentiating piece of capstone work.

## Starting point

- Identity proof reuses the phone OTP from Issue 17.
- The public privacy notice (`src/templates/web/privacy.html`) already promises these rights and names the sections of POPIA; the workflow must match what it says.

## Scope

- Verified request flow: a patient proves control of their phone number by OTP before any data is released
- Machine-readable export of everything held: tickets, visits, consents, notifications, feedback
- Erasure that removes or irreversibly anonymises personal data while preserving aggregate counts
- Staff-side request queue with SLA tracking and full audit
- Documented handling of data the clinic must retain for its own legal reasons

## Out of scope

- Automatic retention purges (Issue 95).

## Acceptance criteria

- [ ] A patient receives a complete machine-readable export after OTP verification
- [ ] Erasure leaves no recoverable personal identifier, proven by a test
- [ ] Aggregate statistics remain correct after an erasure
- [ ] Every request and its outcome is audited with timestamps against the SLA
- [ ] An unverified request releases nothing
- [ ] Legal-retention exceptions are documented and applied consistently

## How to verify

1. Request an export without verifying: nothing is released.
2. Verify and request an export: one machine-readable file with tickets, visits, consents, notifications and feedback.
3. Erase a patient, then re-run the reports: the totals are unchanged and no identifier remains.

## Files touched

- `src/modules/compliance/data_rights.py`
- `src/modules/compliance/router.py`
- `docs/COMPLIANCE/DSAR_PROCEDURE.md`
- `tests/integration/compliance/test_dsar.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #96
