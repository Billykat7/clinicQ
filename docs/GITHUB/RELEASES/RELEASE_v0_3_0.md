# Release v0.3.0: Identity, Auth, RBAC & Consent

**Date:** 2026-09-12 · **Milestone:** M3 · **Issues closed:** 15–22

A pre-release. It adds the trust layer every later feature stands on: two kinds of identity, roles
that mean something, a clinic boundary a query cannot cross by accident, a trail that cannot be
rewritten, and a patient's own answer about what may be done with their name and their number.
There is still no clinic, no queue and no ticket — those are M4 and M6 — so what shipped here is
proven by tests and by a running server, not by a waiting room.

Two decisions shape all of it. **Staff live on the kernel's `user` table**: a second identity table
is how two sign-in paths drift apart, so a staff member's clinic is a scoped role assignment, not a
column. And **a patient has no password on any channel**: a phone number and a one-time code are the
whole identity, because asking for a password before someone can join a queue would end the USSD and
WhatsApp channels (M10) before they start.

## What shipped

- **Staff accounts and the security core** (Issue 15, PR #129). Access tokens are **typed**
  (`type: "access"`) and `decode_access_token` is the only decoder a session is read with, so a
  password-reset link can no longer authenticate as a session — it could before. The token carries
  `sub`, `uid`, `role`, `sites` and a 15-minute expiry, where `sites` is read from site-scoped
  `user_roles` rows, never a column. One identity path (`resolve_active_user` → `get_current_staff`)
  serves the dependency, the RBAC gate, the scope resolver and every kernel router, and refuses a
  deactivated account. Passwords are capped at bcrypt's 72-byte limit by a `NewPassword` type, which
  turns a 500 into a 422. A PostgreSQL test signs in, changes the password and refreshes, then reads
  **every raw row of every table in the schema** and every log record looking for the secrets, and
  finds only bcrypt and SHA-256 digests.
- **Sign-in, sessions, CSRF** (Issue 16, PR #130; migration `0002`). A sign-in starts a token
  **family**; every rotated token joins it. Replaying a token that was already rotated revokes the
  **whole family**, newest token included — not just the token presented — because the server cannot
  tell the thief from the owner; other sessions are untouched, and each reuse writes a
  `SECURITY_AUDIT refresh_token_reuse` line naming the user and session, never the token. A replay
  within `REFRESH_REUSE_GRACE_SECONDS` (5) of the rotation is treated as two tabs racing, not a
  theft. CSRF is three layers: `SameSite=Lax`, Fetch Metadata, and a **signed double-submit token
  bound to the session id**, required on every write that carries the access cookie. Reset links are
  single-use, through a keyed fingerprint of the password they reset.
- **Patient identity** (Issue 17, PR #131; migration `0003`). `src/modules/patients/` in the shape
  of the kernel's own modules: `POST /api/v1/patients/otp/request` (202 and the same answer for
  every number), `POST /otp/verify`, `GET`/`PATCH /patients/me`, `POST /patients/logout`. **One
  normaliser** (`src/commons/phone.py`) makes `0821234567`, `27821234567`, `+27821234567`,
  `+27 (0)82 123 4567` and `0027 82 123 4567` one patient, enforced by a unique `phone_e164`. The
  kernel's OTP store is extended rather than copied: codes are held only as an HMAC, single-use,
  expiring, locked after 5 wrong guesses, with a resend cooldown and a per-number budget. The code
  travels through the `SmsProvider` so it obeys the notification rules, and **no code reaches a log
  or a ledger row** — a test proves it by capturing records at emit, before the redaction filter,
  which is the only way the test could have failed if it were wrong.
- **Roles that mean something** (Issue 18, PR #132). `tenant`, `owner`, `manager` and `vendor` are
  gone, with every grant, test and docstring that named them and the dead portal manifest — no
  aliases. In their place: `patient`, `receptionist`, `nurse_doctor`, `clinic_manager`,
  `platform_admin`, beside the kernel's `user` and `admin`. Grants live in the manifest of what they
  gate (`sites`, `queues`, `patients.self`, `dashboard`), so they are declared where they are read.
  A guard walks the **real router tree** and fails on any `/api/v1` route without a gate, unless it
  is on a closed, reasoned list. `docs/architecture/rbac-matrix.md` is generated (`make rbac-matrix`)
  from a database seeded the way a deployment seeds it, with a drift test, and the decision snapshot
  is a guard again: regenerating it revealed that the committed golden file had been seeded from a
  hand-picked subset and claimed a deployed admin could not open the communications or widgets
  console.
- **One clinic boundary, one helper** (Issue 19, PR #133). `src/core/site_scope.py` is the only way a
  site-scoped row is read. Another clinic's id answers **404 before any permission is considered**,
  so ids cannot be probed; a caller who is at the site but lacks the verb gets 403, because they
  already know it exists. The verb is resolved with the roles held **at that site**, so a manager at
  Clinic B is a receptionist at Clinic A and nothing more. A platform admin's cross-clinic read is an
  explicit, read-only hatch: it needs an `X-ClinicQ-Cross-Site-Reason` header and writes an audit row
  every time; without it, 404 like anyone else. Two guards keep it true — one fails the build when a
  query on a site-scoped model is built outside the helper (the model set is discovered from the
  mappers, so a new `site_id` column is covered with no edit), the other fails when a site-scoped
  resource has no cross-tenant case.
- **The audit trail gets its clinic and its request** (Issue 20, PR #134; migration `0004`).
  `site_id`, `request_id` and `actor_role`, filled from the request context rather than by every call
  site, so a row joins to that request's log lines and to the clinic the guard resolved. Reading is
  split by audience: `GET /sites/{site_id}/audit/events` is one clinic's trail through the site
  guard, `GET /audit/events` is every clinic behind a business-tier grant on a new `audit` resource —
  previously that route was gated on `logs:read`, which conflated "may read the server logs" with
  "may read every clinic's trail". Both audit their own read. Checking the append-only guarantee
  found a hole: the baseline's trigger is row-level and **never fires for `TRUNCATE`**, so one
  statement could have emptied an append-only table. `0004` adds the statement-level guard, and a
  test proves all three refusals against PostgreSQL.
- **Consent** (Issue 21, PR #135; migration `0005`). The answer is **no until the patient says yes**,
  on the web, on USSD, on WhatsApp and at the desk: an unanswered purpose writes no row and reads as
  not granted, so the most private option is the default without anyone setting it. There is one door
  in each direction and a guard test on each: messages resolve through
  `notifications.preferences.resolve`, which now asks `has_consent()` first and **re-asks when a
  queued message is delivered**, so a withdrawal stops a message already in the queue; a public
  screen may show only what `patients.consent.board_projection` returns, which applies the site's
  display mode and the patient's consent together. A sign-in code is exempt — it is the service the
  patient asked for by typing their number. The current answer is one row both surfaces read; every
  answer ever given is kept beside it with its channel, its wording version and the staff member who
  recorded it.
- **Staff invitations and account settings** (Issue 22, PR #136; migration `0006`). A clinic manager
  onboards their own people without the platform team. **The row is the authority; the link is a
  pointer to it** — a typed `staff_invite` JWT carrying an invitation id and nothing else, so a
  forwarded link cannot claim a role, a clinic or a deadline it was not issued for. Single use is the
  row's property: `accepted_at` is set by the same transaction that creates the account, and
  accepted, revoked, expired and never-existed are refused with one message. Nobody hands out a role
  they do not hold, and acceptance writes **one** assignment held at that site. Deactivation is
  immediate in both directions: the next request carrying a still-valid access token is refused, and
  every live refresh token is revoked so no new one can be minted.

## Migrations

Five revisions, `0002` to `0006`, applied in order by `scripts/db/deploy-sequence.sh` before the new
release serves traffic. Each was written so the **previous release keeps running on the new schema**
during a deploy and after a rollback, and each is reversible.

- **`0002_refresh_token_family`** — `family_id` and `rotated_at` on `refresh_token`, both nullable.
  A row written by v0.2.0 has no family; the code reads a missing family as the row's own id, which
  is why `outside_family()` uses `coalesce` rather than a plain inequality (a `NULL` there silently
  matched nothing, and an existing password-change test caught it). Making `family_id` `NOT NULL` is
  a later release's contract step, once no running release can write a row without it.
- **`0003_patients`** — new `patient` table: unique E.164 `phone_e164`, optional `whatsapp_id` and
  `display_name`, a `session_version`, and **no password column anywhere**. The unique constraint is
  what makes one number one patient when two sign-ins race.
- **`0004_audit_site_request`** — `site_id`, `request_id`, `actor_role` on `audit_event` (nullable),
  two indexes, and a statement-level `TRUNCATE` guard. **Nothing is backfilled**: a value cannot be
  invented for a request that is long over, and a backfill is exactly the `UPDATE` the trigger exists
  to refuse. Rows written before this release keep `NULL` and are visible only to a whole-platform
  reader.
- **`0005_patient_consent`** — new `patient_consent` (the current answer) and `patient_consent_event`
  (every answer ever given, with channel, wording version and who recorded it). Two new tables;
  nothing existing changes.
- **`0006_staff_invitation`** — new `staff_invitation`: email, optional phone, site, role, inviter,
  deadline, `accepted_at`, `revoked_at`. Rows are kept after they are used or expire: who invited
  whom is part of a clinic's record of its own staff.

## Upgrade notes

- **The four portal roles are gone.** There is **no migration** for them: anyone holding `tenant`,
  `owner`, `manager` or `vendor` keeps the string on their `user` and `user_roles` rows, but it now
  names a role with no grants, so they reach nothing until reassigned. A clean ClinicQ deployment has
  none. Check with `SELECT role, count(*) FROM clinicq.user_roles GROUP BY role;` before deploying.
- **Seeding is part of a deploy, not an afterthought** (decision 6). `deploy-sequence.sh` runs
  `make seed-rbac` after the migrations and then `seed-rbac-check`; the five roles and their 28
  grants are inserted, and nothing existing is changed. A deployment that skips it has roles with no
  permissions.
- **`GET /audit/events` now needs `audit:read`, not `logs:read`.** The seed grants it to
  `platform_admin` and the kernel `admin`, so anyone who had it keeps it — but a **custom** role
  granted `logs` alone loses that route and needs the new grant from `/admin/rbac`.
- **Every state-changing request from the browser now needs the CSRF header.** Any client that
  writes with the access cookie must send `X-CSRF-Token` from the CSRF cookie; a cross-site unsafe
  request that the browser labels as such is refused outright.
- **Seven new settings**, all with defaults (`.env.example` now lists 118): `REFRESH_REUSE_GRACE_SECONDS`
  (5), `STAFF_INVITE_EXPIRE_HOURS` (72), `OTP_RATE_LIMIT_PER_PHONE` (5), `OTP_MAX_VERIFY_ATTEMPTS`
  (5), `OTP_RESEND_COOLDOWN_SECONDS` (60), `PATIENT_SESSION_HOURS` (12) and
  `PATIENT_SESSION_COOKIE_NAME`. Run `make check-config` after copying; `make env-example`
  regenerates the file.
- **Deactivating a staff member now signs them out immediately**, in the web shell as well as the
  API, and revokes their refresh tokens — so reactivating someone requires them to sign in again.
  Both are intended; the old behaviour let the shell keep rendering until the refresh row expired.
- **Demo staff moved to `@clinicq.example`** (RFC 2606, never delegated). The old seeded addresses
  used `.test`, which `EmailStr` rejects, so seeded staff could never sign in. Re-run
  `make seed-dev-data` on a development database.
- **Read the codes in development at `GET /dev/outbox`** — a route that exists only when
  `ENVIRONMENT=development`. There is no other way to see an OTP, by design.

## Known issues

- **One-time codes are process-local, so the app must run one worker.** The request rate limits are
  on the shared limiter and hold across workers, but the codes themselves live in the process: a
  second worker would reject a code issued by the first. The image runs one worker today. **Moving
  this store to Redis is a prerequisite for scaling out**, and for Issue 102's hosts if they run more
  than one.
- **The consent wording is a draft, and ships as one.** It is written to POPIA s18's plain-language
  expectation and versioned `2026-09-v1-draft`, and the module says so in its docstring — but F owns
  the wording and has not reviewed it. Issue 21's criterion "reviewed and committed" is **committed,
  not reviewed**. Bumping the version after review costs one constant; every answer already records
  the version it was given under.
- **Three audit proofs cannot exist yet.** Issue 20 asks for a test showing a priority reorder, a
  display-mode change and a no-show in the log; none of those routes exists (Issues 46, 27, 43;
  queues and tickets are M4 and M6). What is in place instead is a guard that fails the build when
  any mutating ClinicQ route records nothing, so each is covered the day it lands. This is the one
  M3 acceptance criterion that is not met.
- **`actor_role` is written only when a caller passes it.** The routes here do not yet resolve which
  role the actor was acting as; filling it centrally belongs with the dashboard's site switcher
  (Issue 48), where "which role am I acting as" becomes a real question.
- **There is no `site` table yet.** A site is an id on a role assignment, and site onboarding is
  Issue 23 (M4). Everything site-scoped therefore works against ids the seed invents; the guard, the
  404 rule and the audit column do not change when the table arrives.
- **USSD and WhatsApp trust the gateway's MSISDN with no second OTP** — a deliberate decision, not an
  oversight: written down in the `patients` package docstring and as decision 9 in
  `docs/GITHUB/ISSUES/README.md`. It is worth revisiting if a gateway is ever compromised.
- **An invitation's SMS number is not kept on the account it creates**: `User` has no phone column.
  And `created_at`'s SQLite server default is UTC while this code writes Africa/Johannesburg business
  time — invisible on PostgreSQL, but it makes naive timestamp arithmetic in SQLite tests misleading.
  Both want a sweep of their own.
- **Everything from v0.2.0's list still stands**, unchanged by this release: the credential in the
  repository's history has still not been rotated, nothing is provisioned (no staging or production
  host, no team channel, no error-tracking DSN, no uptime monitor), one person reviews everything,
  nine strict expected failures wait on Issues 97 and the `docs/SECURITY/` documents, a swap is a
  short outage, and the throwaway test tags and demo branches are still on GitHub.

## Verification

Run on `main` at `86d29fe`, with PostgreSQL 18 + PostGIS and Redis 8 in the development stack:

```text
pytest -q -n auto                    1190 passed, 20 skipped, 9 xfailed in 60.02s
pytest -q -m "postgres or redis"     20 passed, 1199 deselected in 37.25s
```

CI ran the same suites in three shards on every one of PRs #129–#136, with `REQUIRE_POSTGRES_TESTS`
and `REQUIRE_REDIS_TESTS` set so a missing service container fails rather than skips, and
`deploy-sequence.sh` applied `0001`–`0006` in order on each.
