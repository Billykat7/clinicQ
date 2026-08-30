# Issue 22: Staff invitations and account settings

**Area:** Backend / Auth
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issues 16, 18
**Estimate:** 2 days
**Status:** Planned

## Context

A clinic manager onboards their own staff; the platform team should not be in that loop. This issue
adds a time-limited invitation flow and the account settings a staff member needs to manage their own
password, name and notification preferences.

## Scope

- Invitation issued by a clinic manager with a role and site, sent by email or SMS, expiring in 72 hours
- Activation flow setting a password and marking the account verified
- Account settings: name, password change, contact details, notification preferences
- Deactivation and reactivation of a staff account without deleting its audit history
- Invitation and activation both audited

## Acceptance criteria

- [ ] A manager can invite a receptionist and the invitation expires after 72 hours
- [ ] An expired or already-used invitation cannot activate an account
- [ ] Deactivating a staff member revokes their sessions immediately
- [ ] A deactivated staff member's past audit rows remain intact and attributable
- [ ] A staff member can change their own password but not their own role
- [ ] Only a clinic manager or platform admin can issue invitations for their site

## Files touched

- `app/services/staff_invitations.py`
- `app/api/staff/invitations.py`
- `app/templates/staff/account.html`
- `tests/integration/test_invitations.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #22
