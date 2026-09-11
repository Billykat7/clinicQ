# Issue 16: Staff sign-in, sessions, logout, CSRF and password reset

> **In short:** Staff sign in, stay signed in safely, see and end their own sessions, and reset a forgotten password, with CSRF protection on every form.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Auth |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 15](../M3/ISSUE_15_staff_user_security_core.md): Staff user model and security core (JWT, refresh rotation, password hashing) |
| **Unblocks** | [Issue 22](../M3/ISSUE_22_staff_invitations_account_settings.md): Staff invitations and account settings<br>[Issue 97](../M13/ISSUE_97_hardening_rate_limits_scanning.md): Hardening: rate limits, security headers, dependency and secret scanning |

## Context

A receptionist signs in once at 07:00 and must stay signed in through a full shift without the session
being stealable. That means a short access token, a rotating refresh cookie with reuse detection, and
CSRF protection, because the dashboard is a cookie-authenticated server-rendered app.

## Starting point

- Most of this exists in `src/api/v1/routes/auth.py`: password sign-in, refresh, logout, forgot and reset password, `GET /auth/me/sessions` and per-session or all-session revocation, with the UI on `/account/security`.
- `src/core/csrf_middleware.py` (double-submit) and the sign-in rate limit (`src/core/rate_limit.py`) are in place.
- Refresh rotation already has reuse detection (the kernel's silent-refresh script, `src/static/js/session-refresh.js`, is built around it) and `src/core/refresh_token_policy.py` adds idle and absolute caps. Confirm a replay revokes the whole token family, as the criteria require.

## Scope

- Sign-in endpoint issuing an access token plus an httpOnly, `SameSite=Lax`, secure refresh cookie
- Refresh rotation with reuse detection that revokes the whole token family on replay
- Sign-out revoking the current session, plus a 'sign out everywhere' action
- Active-sessions list showing device, IP and last used, with per-session revocation
- CSRF double-submit token for every state-changing form and htmx request

## Out of scope

- Staff invitations (Issue 22).
- Patient OTP sign-in (Issue 17).

## Acceptance criteria

- [ ] A successful sign-in sets the refresh cookie with httpOnly, Secure and SameSite attributes
- [ ] Presenting an already-used refresh token revokes the whole family and forces re-authentication
- [ ] A state-changing request without a valid CSRF token is rejected with 403
- [ ] Sign-out invalidates the refresh token server-side, not just client-side
- [ ] Failed sign-ins are rate limited per account and per IP
- [ ] A staff member can see and revoke their own active sessions

## How to verify

1. Sign in and inspect the refresh cookie: `HttpOnly`, `Secure` and `SameSite=Lax` are set.
2. Replay a refresh token that was already used: every session in that family is revoked.
3. Submit a form with the CSRF token removed: 403.
4. `pytest tests/integration/auth` passes.

## Files touched

- `src/api/v1/routes/auth.py`
- `src/core/refresh_token_policy.py`
- `src/core/csrf_middleware.py`
- `alembic/versions/0002_refresh_token_family.py` (the token family)
- `tests/integration/auth/test_auth_signin_sessions.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #16
