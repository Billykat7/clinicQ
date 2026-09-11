# Issue 15: Staff user model and security core (JWT, refresh rotation, password hashing)

> **In short:** Staff can hold an account with a safely hashed password and short-lived tokens; this is the identity every staff-facing route checks.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 2 (weeks 3–4) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Auth |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 3](../M1/ISSUE_3_sqlalchemy_alembic_baseline.md): Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled)<br>[Issue 4](../M1/ISSUE_4_shared_kernel_enums_time_errors.md): Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes |
| **Unblocks** | [Issue 16](../M3/ISSUE_16_staff_signin_sessions_csrf.md): Staff sign-in, sessions, logout, CSRF and password reset<br>[Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification<br>[Issue 18](../M3/ISSUE_18_rbac_roles_enforcement.md): RBAC model, seeded roles and enforcement dependencies<br>[Issue 20](../M3/ISSUE_20_audit_log_admin_api.md): Append-only audit log and admin read API |

## Context

Clinic staff are the only users who sign in with an account. This issue lands the `staff_users` model
and the security primitives every protected route depends on: bcrypt password hashing, a short-lived
access JWT, and an opaque refresh token stored only as a hash so a database leak cannot mint sessions.

## Starting point

- Largely built: the `users` table (`src/database/models/user.py`), hashed `refresh_token` rows, and `src/core/security.py` (bcrypt, HS256 access tokens, auth dependencies), plus the production guard on the development secret in `src/core/config.py`.
- Reuse `users` for staff rather than adding a `staff_users` table. A staff member's site belongs on a scoped role assignment (`UserRoleAssignment` with `scope_type='site'`, see Issue 19), not a column on the user.
- What is left: check each acceptance criterion against the kernel, and add the site claim to the access token if the team wants it there.

## Scope

- Staff accounts are the kernel's `user` table: id, email, password hash, role mirror, active, verified, last login, timestamps. **No `staff_users` table** (decided in this issue): a second identity table is how two sign-in paths drift apart. A staff member's site is a role assignment scoped to that site (`user_roles` with `scope_type='site'`), never a column on the user
- `refresh_token` stores a SHA-256 hash, expiry, revocation and device metadata
- `src/core/security.py`: password hashing/verification, access-token creation and decoding, typed token claims
- `get_current_staff()` dependency resolving an active account from a Bearer header or an httpOnly access cookie
- Production guard refusing to boot with the development signing secret

## Out of scope

- Sign-in, sessions and password reset flows (Issue 16).
- Patients, who have no password at all (Issue 17).
- Roles and permissions (Issue 18).

## Acceptance criteria

- [ ] `alembic upgrade head` creates `user` and `refresh_token` (the staff tables; see Scope)
- [ ] Passwords round-trip through bcrypt at cost 12 and are never stored or logged in plain text
- [ ] An access token is a decodable HS256 JWT carrying subject, role, site and expiry
- [ ] Refresh tokens are unrecoverable from the database (hash only)
- [ ] `get_current_staff()` works from both a Bearer header and a cookie
- [ ] The app refuses to start in production with the default secret

## How to verify

1. `make migrate-up`, then inspect `users` and `refresh_token`: no plaintext password or token column exists.
2. Decode an access token from a sign-in: it carries subject, role and expiry.
3. Start with `ENVIRONMENT=production` and the default secret: the app refuses to boot.

## Files touched

- `src/database/models/user.py`
- `src/database/models/refresh_token.py`
- `src/core/security.py`
- `src/core/config.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #15
