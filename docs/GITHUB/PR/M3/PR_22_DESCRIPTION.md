# PR: A clinic invites its own staff, and can switch an account off at once (Issue 22 / M3-22)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#22](https://github.com/Billykat7/clinicQ/issues/22) · **Builds on:** #15–#21 (PRs #129–#135) ·
**Closes the milestone.**

> **Merge order:** after #129–#135; the Conventions check fails on their commits until then.

Half of this issue was already built and only needed checking: account settings exist
(`PATCH /auth/me`, `POST /auth/me/password`, `/auth/me/email`, the session list, notification
preferences), and deactivation was already immediate for the API — Issue 15's `find_active_user`
refuses a deactivated account's still-valid access token on the next request. What did not exist is
invitations, and checking the rest turned up one real gap:

- **The HTML shell never checked `is_active`.** `nav_visibility.peek_user_from_refresh_cookie`
  resolved the signed-in user from the refresh cookie with no `is_active` / `is_deleted` filter, so
  a deactivated staff member kept being rendered the dashboard and `/account/*` until their refresh
  row expired. Deactivation now revokes those rows, and the shell checks the flag as well, because
  a page rendered for someone switched off should not depend on a revocation having happened.

## Summary

- **`staff_invitation` (migration `0006`):** email, optional phone, site, role, who invited, the
  deadline, and `accepted_at` / `revoked_at`. **The row is the authority; the link is just a
  pointer to it** — a typed `staff_invite` JWT carrying an invitation id and nothing else, so a
  forwarded or edited link cannot claim a role, a clinic or a deadline it was not issued for.
- **Single use, on the row.** `accepted_at` is set in the same transaction that creates the
  account. Accepted, revoked, expired and never-existed are refused with **one** message.
- **Nobody hands out a role they do not hold.** `INVITABLE_ROLES` says who may issue each role: a
  clinic manager staffs a clinic; only a platform admin makes another platform admin. Enforced on
  the server, not by hiding a field.
- **Acceptance grants a role *at a site*:** one `user_roles` row with `scope_type='site'` and the
  invitation's clinic — never an unscoped assignment, which would make a receptionist at one clinic
  a receptionist at all of them. An address that already has an account keeps its own password and
  gains only the role.
- **Deactivation is immediate in both directions:** `is_active` false refuses the next request
  carrying a still-valid access token, **and** every live refresh token is revoked so no new one
  can be minted. Nobody may switch off their own account.
- **`/invite`**, the page someone with no account opens, and `/api/v1/staff/invitations/{preview,accept}`.
- **Audited throughout:** the invitation, the acceptance and the deactivation each write a row with
  the clinic on it — and never the link, which is a credential.

## Design notes

**Why a row rather than a self-contained token.** The password-reset link is single use without a
table, through the password fingerprint (Issue 16) — a neat trick that works because a reset
*changes the thing the fingerprint is taken of*. An invitation has no such anchor before the account
exists, and it carries authority (a role, at a clinic) that must be revocable and auditable before
it is spent. So: a row, with the link naming it. That also makes "what is outstanding at this
clinic" a query rather than an unknowable.

**The activation link was not a model to copy.** `GET /auth/activate` is *idempotent*, not single
use — it sets `is_verified` and can be replayed for its whole lifetime, which is harmless for a
flag and would not be for something that sets a password and grants a role.

**Accepting does not sign you in.** The account is created and the person signs in with it. A flow
that mints a credential *and* hands out a session turns one stolen link into a live session, and
sign-in is where the rate limit and the "someone signed in" audit line already live.

**Four refusals, one message.** Used, revoked, expired, never existed — `This invitation link is no
longer valid.` The deadline is checked against **the row**, not only the JWT's `exp`, so it holds
even if a token were ever minted with a longer life.

**The exception in the site-scope guard, and why it is one line wide.** Every query in the module
goes through Issue 19's helpers except `usable_invitation`, which is reached by someone with no
session and therefore no clinic to be scoped by. Rather than exempt the file, the guard now takes a
`path::function` entry with a reason, and a fixture proves the *next* query in the same file is
still a finding.

**Only a clinic manager or platform admin, and only for their site** is enforced by two existing
things rather than new code: the `sites.staff` grant (a receptionist holds `read`, a manager holds
through `delete`) and the site guard (another clinic is a 404). A consequence worth naming: a
platform admin can invite only where they hold their role, because Issue 19 refuses cross-site
*writes* even with the reason header. Bootstrapping the first manager of a brand-new clinic
therefore belongs with site onboarding (Issue 23) — there is a test asserting today's behaviour so
the decision is visible rather than discovered later.

**The invitation link is treated as a secret**: withheld from the SMS ledger payload
(`NOTIFICATION_SECRET_FIELDS`), absent from every response body including the inviter's, absent
from the audit context, and redacted by the logging filter even in the development log line.

**Role wording moved to one table.** `role_label()` in `rbac_language.py`, so a page never shows
`nurse_doctor` and the email and the API cannot drift into two vocabularies.

**Out of scope:** assigning staff to rooms and queues (Issue 28), the clinic's own staff management
screen (the dashboard shell, M4).

## Changes

- **`alembic/versions/0006_staff_invitation.py`** (new), **`src/database/models/staff_invitation.py`**
  (new). `invited_by` is `RESTRICT`: a deactivated account is still the author of what it issued.
- **`src/modules/staff/invitations.py`** (new): `invite_staff`, `deliver_invitation`,
  `usable_invitation`, `accept_invitation`, `revoke_invitation`, `list_invitations`,
  `invitation_state` (derived, so no status column can fall out of step) and `set_staff_active`.
- **`src/modules/staff/router.py` / `schemas.py`:** the five site-scoped routes and the two public
  ones. Invitation routes are declared **before** `/{site_id}/staff/{user_id}`, or FastAPI matches
  `invitations` as a user id — caught by the cross-tenant suite.
- **`src/core/security.py`:** `create_staff_invite_token` / `decode_staff_invite_token`, and
  `account_for_email` (the shared "is there an account at this address", deliberately not filtered
  by `is_active`).
- **`src/core/nav_visibility.py`:** the `is_active` gap above.
- **`src/core/config.py`:** `STAFF_INVITE_EXPIRE_HOURS` (72). **`src/core/email_send.py`:**
  `send_staff_invitation_email`. **`src/modules/notifications/templates.py`:** the SMS renderer.
  **`src/commons/enums.py`:** the token type, the audit entity, the template and its category.
- **`src/templates/account/invitation.html`, `src/static/js/staff-invitation.js`,
  `src/static/css/layouts.css`, `src/web/routes.py`:** the `/invite` page.
- **`tests/`:** `integration/staff/test_invitations.py` (new, 24 cases), the `staffinvitation` case
  in the cross-tenant suite, and the two guard lists (the public routes, the site-scope exception)
  each with their reason.
- **No RBAC snapshot change**: `sites.staff` and its grants already existed (Issue 18/19); this PR
  adds routes under them, not permissions.

## Testing

- [x] `ruff check`, `ruff format --check`, `mypy src/` (185 files) clean.
- [x] `make test`: **1190 passed**, 20 skipped, 9 xfailed. PostgreSQL and Redis: **20 passed**
      (migration `0006` round-trips and autogenerate finds no drift).
- [x] **The tests were themselves tested.** Three mutations, each caught by a different case:
      not closing the invitation on acceptance, ignoring the row's deadline, and — after the first
      version of the deactivation test passed without it — not revoking the refresh families. That
      last one is why the test now reads the rows *before* anything touches `/auth/refresh`: the
      refresh endpoint revokes on its own, and the original assertion was measuring that instead.
- [x] **How to verify, on a real server** (PostgreSQL migrated to `0006`, `make seed-dev-data`):

```text
  manager invites a receptionist at their own clinic   POST /sites/hillbrow-chc/staff/invitations
    -> 201  {"email": "naledi@clinicq.example", "role": "receptionist",
             "site_id": "hillbrow-chc", "state": "pending", "expires_at": "2026-09-14T23:07:49Z"}
             ↑ 72 hours, and no token anywhere in the response

  receptionist invites                                                        -> 403
  manager invites into another clinic                                         -> 404
  manager invites a platform admin                                            -> 403

  the link reaches the person invited, and nothing else:
    SMS   "BK ClinicQ: you have been invited to join a clinic. Open http://…/invite?token=…"
    log   "Invitation link (dev): http://127.0.0.1:8772/invite?token=[REDACTED:token]"
    ledger  staff_invitation | +27821112222 | sent | {"link": "[withheld: one-time code]", "hours": 72}

  GET  /api/v1/staff/invitations/preview   (no session)  -> 200 {email, role, role_label, site_id, expires_at}
  POST /api/v1/staff/invitations/accept                  -> 200 "Your account is ready."
  the same link again                                    -> "This invitation link is no longer valid."
  a token that never existed                             -> "This invitation link is no longer valid."
                                                              ↑ the same sentence, on purpose

  she signs in with the password she chose               -> 200
  GET /auth/me with her access token                     -> 200
  manager deactivates her                                -> 200  is_active = False
  GET /auth/me, the SAME token, still signed, unexpired  -> 401

$ psql:
  user         naledi@clinicq.example | is_active f | is_verified t | password $2b$12$ | receptionist
  user_roles   receptionist | site | hillbrow-chc          ← one assignment, held at the clinic
  invitation   used t | revoked f | expires 2026-09-14 23:07:49+00
  sessions     live_sessions 0  (of 1 row)                 ← revoked by the deactivation itself

  action | entity_type       | actor                   | site_id      | context
  create | staff_invitation  | manager@clinicq.example | hillbrow-chc | invited naledi@… as receptionist
  update | staff_invitation  | naledi@clinicq.example  | hillbrow-chc | accepted as receptionist (new account)
  update | user              | manager@clinicq.example | hillbrow-chc | deactivated; live sessions revoked
```

- [x] **Screenshot** — `/invite`, with a real pending invitation loaded. Files in
      `docs/GITHUB/PR/M3/assets/pr22/`.

| Light | Dark |
|---|---|
| ![The invitation acceptance page, light](https://github.com/Billykat7/clinicQ/blob/617fe360f2e6d6214723e7f76a5564b13b9ae62f/docs/GITHUB/PR/M3/assets/pr22/invite-light.png?raw=true) | ![The invitation acceptance page, dark](https://github.com/Billykat7/clinicQ/blob/617fe360f2e6d6214723e7f76a5564b13b9ae62f/docs/GITHUB/PR/M3/assets/pr22/invite-dark.png?raw=true) |

## Acceptance criteria

- [x] **A manager can invite a receptionist and the invitation expires after 72 hours.** Shown
      above; the deadline is `STAFF_INVITE_EXPIRE_HOURS` (72) and is checked against the row.
- [x] **An expired or already-used invitation cannot activate an account.** Both tested, along with
      a revoked one and a token that never existed — all four answer identically, and the replay
      grants nothing (asserted on the assignments, not only on the status code).
- [x] **Deactivating a staff member revokes their sessions immediately.** Proven with a request
      carrying a still-valid access token (401), with the refresh rows read before anything else
      touches them (0 live), and with the HTML shell's own `is_active` check added.
- [x] **A deactivated staff member's past audit rows remain intact and attributable.** The account
      row is kept (`is_active` false, not deleted), the trail is byte-for-byte unchanged, and the
      invitation's `invited_by` is `RESTRICT` so authorship survives.
- [x] **A staff member can change their own password but not their own role.** The password change
      succeeds; `role` smuggled into `PATCH /auth/me` changes neither the column nor the
      assignments; the RBAC assignment API is refused to them. All three in one test.
- [x] **Only a clinic manager or platform admin can issue invitations for their site.** The grant
      decides who, the site guard decides where, and both halves are tested — including that a
      platform admin not assigned to the clinic is refused, which is Issue 19's rule holding here.

## Risk and rollback

**Migration `0006`** adds one table and touches nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible. Two behaviour changes: a deactivated
staff member is now signed out of the **web shell** as well as the API (previously the shell kept
rendering until their refresh row expired), and deactivation revokes refresh tokens, so
reactivating someone requires them to sign in again — both intended.

**Follow-ups noticed:** there is no phone column on `User`, so an invitation's SMS number is not
kept on the account it creates; bootstrapping the first manager of a new clinic needs site
onboarding (Issue 23); and `created_at`'s SQLite server default is UTC while this code writes
business time, which is invisible in PostgreSQL but makes naive timestamp arithmetic misleading in
SQLite tests — worth a sweep of its own rather than a change here.

Closes #22
