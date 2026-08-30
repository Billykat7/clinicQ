# Issue 97: Hardening: rate limits, security headers, dependency and secret scanning

**Area:** Backend / Security
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 16, 40
**Estimate:** 3 days
**Status:** Planned

## Context

Public endpoints on a health service are a target, and a student project is not exempt. This issue
closes the routine holes: unbounded endpoints, missing headers, vulnerable dependencies, committed
secrets, so the penetration test in Issue 100 can spend its time on the interesting ones.

## Scope

- Rate limiting per IP, per phone number and per site on every public endpoint
- Security headers: CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, frame options
- CSRF verified on every state-changing route, checked by a guard test
- `pip-audit` and secret scanning in CI, failing the build on a finding
- Input validation and output escaping reviewed across every user-supplied field

## Acceptance criteria

- [ ] Every public endpoint is rate limited, verified by a guard test enumerating the routes
- [ ] Security headers are present on every response and validated by an automated scan
- [ ] A known-vulnerable dependency fails CI
- [ ] A committed secret fails CI
- [ ] The CSP is strict enough to block inline script and is not weakened by a wildcard
- [ ] Free-text fields are escaped on every rendering surface including the board

## Files touched

- `.github/workflows/security.yml`
- `app/middleware/rate_limit.py`
- `app/middleware/security_headers.py`
- `tests/security/test_headers.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #97
