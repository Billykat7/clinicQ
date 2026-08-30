# Milestone 3: Identity, Auth, RBAC & Consent

**Status:** 📋 planned · **Phase:** Semester 1 · Sprint 3–4 · **Suggested tag:** `v0.3.0`
**Primary owner:** Backend Lead
**Depends on:** M1
**Blocks:** M4, M7, M12, M13; every authenticated surface and every site-scoped query. Ship the RBAC dependency and the site-scoping guard **first** inside this milestone so the dashboard and clinic-admin work can start against them.

## Goal

Give the platform two distinct identities: **staff**, who sign in with an account, and **patients**, who are identified by a phone number and an OTP, plus role-based access control, hard multi-tenant site scoping, an append-only audit log, and POPIA-grade consent capture.

## Why this milestone exists

ClinicQ is multi-tenant from the first pilot: two clinics share one database, and a receptionist at
Clinic A must never see a ticket at Clinic B. Retrofitting that guarantee later means auditing every
query in the codebase, so the **site-scoping dependency lands here**, before there is anything to
scope.

Patient identity is deliberately **not** an account. Asking someone to create a password before they
can join a queue kills the USSD and WhatsApp channels outright (M10) and most of the walk-in
population with them. A phone number plus a one-time PIN is the whole identity, and consent flags
ride on the patient record because the display board (M8) and the notification service (M9) both
have to check them before they act.

## Scope

- `staff_users` model, password hashing, short-lived access JWT + rotating refresh cookie, CSRF
- Staff sign-in, sessions list, logout, password reset and staff invitation flow
- Patient identity: phone-first records, OTP verification, rate-limited resend, no password
- RBAC: roles `patient`, `receptionist`, `nurse_doctor`, `clinic_manager`, `platform_admin`; verb-based permissions enforced by FastAPI dependencies
- Multi-tenancy: a `require_site_access` dependency and a query guard so cross-site reads are impossible
- Append-only audit log with actor, action, target, site and request id, plus an admin read API
- Consent model: display consent, notification consent, comment-on-board consent, each with capture and withdrawal

## Issues

| # | Title |
|---|-------|
| 15 | Staff user model and security core (JWT, refresh rotation, password hashing) |
| 16 | Staff sign-in, sessions, logout, CSRF and password reset |
| 17 | Patient identity: phone-first records with OTP verification |
| 18 | RBAC model, seeded roles and enforcement dependencies |
| 19 | Multi-tenant site scoping guard and cross-site access tests |
| 20 | Append-only audit log and admin read API |
| 21 | Consent capture and withdrawal (display, notifications, board comment) |
| 22 | Staff invitations and account settings |

## Exit criteria

- [ ] A clinic manager can invite a receptionist, who activates the account and signs in
- [ ] Access tokens expire in 15 minutes; refresh rotates and detects reuse; logout revokes
- [ ] A patient joins a queue with a phone number and a 6-digit OTP, with no account created
- [ ] A receptionist at Clinic A receives 404 (not 403) for any Clinic B resource, proven by tests
- [ ] Every state-changing action writes one audit row that cannot be updated or deleted
- [ ] Consent is stored per patient per purpose, is withdrawable, and withdrawal takes effect immediately

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M3/)
