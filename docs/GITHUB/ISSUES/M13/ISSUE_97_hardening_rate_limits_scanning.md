# Issue 97: Hardening: rate limits, security headers, dependency and secret scanning

> **In short:** The public surface is hardened: rate limits everywhere, strict browser security headers, and CI that stops a vulnerable dependency or a leaked secret.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 8 (weeks 15–16) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Backend / Security |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 16](../M3/ISSUE_16_staff_signin_sessions_csrf.md): Staff sign-in, sessions, logout, CSRF and password reset<br>[Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards |
| **Unblocks** | [Issue 100](../M13/ISSUE_100_pen_test_remediation.md): Authorised penetration test of staging and remediation |

## Context

Public endpoints on a health service are a target, and a student project is not exempt. This issue
closes the routine holes: unbounded endpoints, missing headers, vulnerable dependencies, committed
secrets, so the penetration test in Issue 100 can spend its time on the interesting ones.

## Starting point

- Much is already there: `src/core/security_headers.py` (a strict CSP with a per-request nonce), `src/core/csrf_middleware.py`, `src/core/rate_limit.py`, and pip-audit, Trivy and a secret scan in `scripts/ci-local.sh`.
- pip-audit is warn-only locally; the criteria want a CI failure, so the new workflow must be stricter than the local script.
- What is new: rate limits on the ClinicQ public endpoints (search, join, OTP, channel webhooks) and a guard test that enumerates them.

## Scope

- Rate limiting per IP, per phone number and per site on every public endpoint
- Security headers: CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, frame options
- CSRF verified on every state-changing route, checked by a guard test
- `pip-audit` and secret scanning in CI, failing the build on a finding
- Input validation and output escaping reviewed across every user-supplied field

## Out of scope

- The penetration test itself (Issue 100).
- Edge protection at the reverse proxy (Issue 102).

## Acceptance criteria

- [ ] Every public endpoint is rate limited, verified by a guard test enumerating the routes
- [ ] Security headers are present on every response and validated by an automated scan
- [ ] A known-vulnerable dependency fails CI
- [ ] A committed secret fails CI
- [ ] The CSP is strict enough to block inline script and is not weakened by a wildcard
- [ ] Free-text fields are escaped on every rendering surface including the board

## How to verify

1. Add a public route without a limiter: the guard test fails.
2. Run a header scanner against staging: every response carries the headers, and the CSP has no wildcard.
3. Pin a known-vulnerable package version on a branch: CI fails.

## Files touched

- `.github/workflows/security.yml`
- `src/core/rate_limit.py`
- `src/core/security_headers.py`
- `tests/unit/security/test_headers.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #97
