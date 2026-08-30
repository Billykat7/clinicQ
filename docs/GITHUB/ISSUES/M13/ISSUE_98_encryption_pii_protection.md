# Issue 98: Encryption in transit and at rest, field-level encryption for patient contacts

**Area:** Backend / Security
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 17, 95
**Estimate:** 2 days
**Status:** Planned

## Context

A phone number in this system links a person to a clinic, a date and sometimes a stated symptom. That
combination is worth protecting at rest, so an accidental dump or a stolen backup does not become a
disclosure.

## Scope

- TLS enforced end to end, with HTTP redirected and HSTS preloaded
- Encryption at rest for the database and for backups
- Field-level encryption for patient phone numbers and WhatsApp ids, with a searchable blind index
- Key management with documented rotation, and keys never in the repository
- A test asserting that a raw table dump exposes no readable phone number

## Acceptance criteria

- [ ] A raw database dump contains no readable patient phone number
- [ ] Lookup by phone number still works at full speed via the blind index
- [ ] Backups are encrypted and the restore path with keys has been exercised
- [ ] Key rotation is documented and has been performed once in staging
- [ ] All traffic is HTTPS, with HTTP redirected
- [ ] No key material exists anywhere in the repository history

## Files touched

- `app/core/crypto.py`
- `app/database/models/patient.py`
- `docs/COMPLIANCE/KEY_MANAGEMENT.md`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #98
