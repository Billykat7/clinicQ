# Issue 101: WCAG 2.2 AA accessibility audit and remediation

**Area:** Frontend / Accessibility
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issues 59, 68
**Estimate:** 3 days
**Status:** Planned

## Context

A health queue that excludes patients with disabilities fails at its purpose. This is a full audit
across all three surfaces: automated checks plus manual keyboard and screen-reader testing, with the
findings fixed rather than merely listed.

## Scope

- Automated WCAG 2.2 AA checks in CI across patient pages, dashboard and board
- Manual keyboard-only and screen-reader testing of every critical flow
- Target sizes, focus visibility, form labelling and error identification reviewed
- Remediation of every AA failure, with any accepted exception documented
- An accessibility statement published with the product

## Acceptance criteria

- [ ] Automated checks pass at AA on every key page, enforced in CI
- [ ] Join, check status and cancel are completable with a keyboard alone
- [ ] A screen-reader user can complete the join flow, verified by a recorded test
- [ ] Focus is always visible and never trapped
- [ ] Every remaining exception is documented with a reason and a plan
- [ ] The accessibility statement is published and accurate

## Files touched

- `tests/a11y/`
- `app/templates/`
- `docs/COMPLIANCE/ACCESSIBILITY_STATEMENT.md`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #101
