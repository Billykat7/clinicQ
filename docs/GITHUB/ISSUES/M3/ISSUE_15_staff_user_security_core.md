# Issue 15: Staff user model and security core (JWT, refresh rotation, password hashing)

**Area:** Backend / Auth
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issues 3, 4
**Estimate:** 3 days
**Status:** Planned

## Context

Clinic staff are the only users who sign in with an account. This issue lands the `staff_users` model
and the security primitives every protected route depends on: bcrypt password hashing, a short-lived
access JWT, and an opaque refresh token stored only as a hash so a database leak cannot mint sessions.

## Scope

- `staff_users` model: id, site_id, email, password hash, role, active, verified, last login, timestamps
- `refresh_tokens` model storing a SHA-256 hash, expiry, revocation and device metadata
- `app/core/security.py`: password hashing/verification, access-token creation and decoding, typed token claims
- `get_current_staff()` dependency resolving a user from a Bearer header or an httpOnly access cookie
- Production guard refusing to boot with the development signing secret

## Acceptance criteria

- [ ] `alembic upgrade head` creates `staff_users` and `refresh_tokens`
- [ ] Passwords round-trip through bcrypt at cost 12 and are never stored or logged in plain text
- [ ] An access token is a decodable HS256 JWT carrying subject, role, site and expiry
- [ ] Refresh tokens are unrecoverable from the database (hash only)
- [ ] `get_current_staff()` works from both a Bearer header and a cookie
- [ ] The app refuses to start in production with the default secret

## Files touched

- `app/database/models/staff_user.py`
- `app/database/models/refresh_token.py`
- `app/core/security.py`
- `migrations/versions/*_staff_auth.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #15
