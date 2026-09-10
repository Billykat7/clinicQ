# Issue 22: Staff invitations and account settings

> **In short:** A clinic manager can invite staff, and staff can manage their own account, without anyone touching the database.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 4 (weeks 7–8) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Auth |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 16](../M3/ISSUE_16_staff_signin_sessions_csrf.md): Staff sign-in, sessions, logout, CSRF and password reset<br>[Issue 18](../M3/ISSUE_18_rbac_roles_enforcement.md): RBAC model, seeded roles and enforcement dependencies |
| **Unblocks** | [Issue 28](../M4/ISSUE_28_staff_site_room_assignment.md): Staff-to-site and room assignment |

## Context

A clinic manager onboards their own staff; the platform team should not be in that loop. This issue
adds a time-limited invitation flow and the account settings a staff member needs to manage their own
password, name and notification preferences.

## Starting point

- Account settings exist: `/account/profile`, `/account/security` (password and sessions) and `/account/notifications`.
- The signup and activation flow (`POST /auth/signup`, `GET /auth/activate`) is the model for accepting an invitation; there is no invitation model yet.
- Deactivation can use the existing `is_active` flag on `users` (`ActiveMixin`).

## Scope

- Invitation issued by a clinic manager with a role and site, sent by email or SMS, expiring in 72 hours
- Activation flow setting a password and marking the account verified
- Account settings: name, password change, contact details, notification preferences
- Deactivation and reactivation of a staff account without deleting its audit history
- Invitation and activation both audited

## Out of scope

- Assigning staff to sites and rooms (Issue 28).
- Patient accounts (Issue 17).

## Acceptance criteria

- [ ] A manager can invite a receptionist and the invitation expires after 72 hours
- [ ] An expired or already-used invitation cannot activate an account
- [ ] Deactivating a staff member revokes their sessions immediately
- [ ] A deactivated staff member's past audit rows remain intact and attributable
- [ ] A staff member can change their own password but not their own role
- [ ] Only a clinic manager or platform admin can issue invitations for their site

## How to verify

1. Invite a receptionist, wait past 72 hours (or move the clock in a test): the link no longer works.
2. Deactivate a staff member who is signed in: their next request fails.
3. Try to change your own role from account settings: the option does not exist and the API refuses.

## Files touched

- `src/modules/staff/invitations.py`
- `src/modules/staff/router.py`
- `src/templates/account/`
- `tests/integration/staff/test_invitations.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #22
