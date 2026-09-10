# Issue 101: WCAG 2.2 AA accessibility audit and remediation

> **In short:** Everyone can use ClinicQ, including people who rely on a keyboard, a screen reader or large targets, and this is checked in CI.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Accessibility |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 59](../M8/ISSUE_59_board_accessibility.md): Accessibility pass: contrast, type scale, 5-metre legibility, reduced motion<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel |
| **Unblocks** | No other issue waits on this one. |

## Context

A health queue that excludes patients with disabilities fails at its purpose. This is a full audit
across all three surfaces: automated checks plus manual keyboard and screen-reader testing, with the
findings fixed rather than merely listed.

## Starting point

- The board's own accessibility pass (Issue 59) is done by now; this covers the patient pages and the dashboard.
- Browser test tooling from Issue 55 can run the automated checks in CI.

## Scope

- Automated WCAG 2.2 AA checks in CI across patient pages, dashboard and board
- Manual keyboard-only and screen-reader testing of every critical flow
- Target sizes, focus visibility, form labelling and error identification reviewed
- Remediation of every AA failure, with any accepted exception documented
- An accessibility statement published with the product

## Out of scope

- Content translation (Issue 77).

## Acceptance criteria

- [ ] Automated checks pass at AA on every key page, enforced in CI
- [ ] Join, check status and cancel are completable with a keyboard alone
- [ ] A screen-reader user can complete the join flow, verified by a recorded test
- [ ] Focus is always visible and never trapped
- [ ] Every remaining exception is documented with a reason and a plan
- [ ] The accessibility statement is published and accurate

## How to verify

1. Run the automated checks in CI: every key page passes at AA.
2. Join, check status and cancel using only the keyboard: all possible, focus always visible.
3. Record a screen-reader run through the join flow and link it in the PR.

## Files touched

- `tests/a11y/`
- `src/templates/`
- `docs/COMPLIANCE/ACCESSIBILITY_STATEMENT.md`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #101
