# Issue 98: Encryption in transit and at rest, field-level encryption for patient contacts

> **In short:** Patients' phone numbers are unreadable in the database and in backups, yet lookup by phone number is still instant.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 9 (weeks 17–18) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Backend / Security |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification<br>[Issue 95](../M13/ISSUE_95_data_map_retention_purge.md): Data map, retention policy and automatic purge jobs |
| **Unblocks** | [Issue 100](../M13/ISSUE_100_pen_test_remediation.md): Authorised penetration test of staging and remediation<br>[Issue 102](../M14/ISSUE_102_production_infra_tls.md): Production infrastructure, TLS, domains and edge protection |

## Context

A phone number in this system links a person to a clinic, a date and sometimes a stated symptom. That
combination is worth protecting at rest, so an accidental dump or a stolen backup does not become a
disclosure.

## Starting point

- `src/core/encryption.py` already provides Fernet field encryption (`EncryptedString`) with key rotation (tested in `tests/unit/security/test_encryption_rotation.py`).
- What is new: a blind index (a keyed hash of the normalised number) so the patient lookup does not need to decrypt every row.
- `scripts/db/backup.sh` already encrypts dumps with `age`.

## Scope

- TLS enforced end to end, with HTTP redirected and HSTS preloaded
- Encryption at rest for the database and for backups
- Field-level encryption for patient phone numbers and WhatsApp ids, with a searchable blind index
- Key management with documented rotation, and keys never in the repository
- A test asserting that a raw table dump exposes no readable phone number

## Out of scope

- Production TLS and domains (Issue 102).

## Acceptance criteria

- [ ] A raw database dump contains no readable patient phone number
- [ ] Lookup by phone number still works at full speed via the blind index
- [ ] Backups are encrypted and the restore path with keys has been exercised
- [ ] Key rotation is documented and has been performed once in staging
- [ ] All traffic is HTTPS, with HTTP redirected
- [ ] No key material exists anywhere in the repository history

## How to verify

1. `pg_dump` the patients table: no readable phone number.
2. Look a patient up by phone number against 10,000 seeded rows: the index is used, and it is fast.
3. Rotate the key in staging and follow `KEY_MANAGEMENT.md`: existing numbers still decrypt.

## Files touched

- `src/core/encryption.py`
- `src/database/models/patient.py`
- `alembic/versions/NNNN_patient_phone_encryption.py`
- `docs/COMPLIANCE/KEY_MANAGEMENT.md`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #98
