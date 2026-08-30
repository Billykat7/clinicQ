# Issue 16: Staff sign-in, sessions, logout, CSRF and password reset

**Area:** Backend / Auth
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issue 15
**Estimate:** 2 days
**Status:** Planned

## Context

A receptionist signs in once at 07:00 and must stay signed in through a full shift without the session
being stealable. That means a short access token, a rotating refresh cookie with reuse detection, and
CSRF protection, because the dashboard is a cookie-authenticated server-rendered app.

## Scope

- Sign-in endpoint issuing an access token plus an httpOnly, `SameSite=Lax`, secure refresh cookie
- Refresh rotation with reuse detection that revokes the whole token family on replay
- Sign-out revoking the current session, plus a 'sign out everywhere' action
- Active-sessions list showing device, IP and last used, with per-session revocation
- CSRF double-submit token for every state-changing form and htmx request

## Acceptance criteria

- [ ] A successful sign-in sets the refresh cookie with httpOnly, Secure and SameSite attributes
- [ ] Presenting an already-used refresh token revokes the whole family and forces re-authentication
- [ ] A state-changing request without a valid CSRF token is rejected with 403
- [ ] Sign-out invalidates the refresh token server-side, not just client-side
- [ ] Failed sign-ins are rate limited per account and per IP
- [ ] A staff member can see and revoke their own active sessions

## Files touched

- `app/api/auth/staff.py`
- `app/services/auth.py`
- `app/middleware/csrf.py`
- `tests/integration/test_staff_auth.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #16
