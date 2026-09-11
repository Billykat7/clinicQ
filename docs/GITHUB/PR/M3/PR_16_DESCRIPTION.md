# PR: Staff sessions that survive a shift and not a theft: token families, session-bound CSRF, single-use reset links (Issue 16 / M3-16)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#16](https://github.com/Billykat7/clinicQ/issues/16) · **Builds on:** #15 (PR #129)

> **Merge order:** after #129. Until #129 is merged this branch also carries its two `Issue 15:`
> commits, so the Conventions check fails on them; re-run it once #129 is in.

Almost all of Issue 16 existed: password and OTP sign-in, refresh rotation with a reuse check,
sign-out, forgot and reset password, the sessions list with per-session and all-session revocation,
the CSRF middleware and the sign-in rate limit. So the work was to check each criterion on a real
response. The one flagged as likely half true was: **refresh rotation had no idea what a token
family was.** Here is what `main` actually did, from a probe run on a clean `main` worktree:

```text
1. Device A revokes device B's session; then device B's silent refresh runs
   A revokes B -> 204     B refreshes -> 401     A refreshes -> 401   (A signed itself out)
2. Revoke a session by the id the list showed, after that session's token rotated
   A revokes B -> 204     B refreshes -> 200   (the revoke landed on a spent row; B still in)
3. A stolen, already-rotated token is replayed
   thief -> 401     owner -> 401     the owner's other device -> 401   (every session gone)
4. A cookie-authenticated write whose CSRF cookie is missing
   PATCH /auth/me -> 200   (no CSRF cookie meant no check)
5. A password-reset link used twice
   first use -> 200     second use -> 200
```

The reuse check revoked *every* session the user had, and it could not tell a replayed token from
one that had simply been revoked, so ordinary session management (1) signed people out of the
device they were using. The sessions list named rows, not sessions, so a revoke raced by a silent
refresh did nothing (2). The same probe on this branch:

```text
1.  A revokes B -> 204     B refreshes -> 401     A refreshes -> 200
2.  A revokes B -> 204     B refreshes -> 401
3.  thief -> 401     owner -> 401 (the family: sign in again)     other device -> 200
4.  PATCH /auth/me -> 403
5.  first use -> 200     second use -> 400
```

## Summary

- **Token families** (migration `0002`): a sign-in starts a family; every rotated token joins it
  (`family_id`), and a spent token is marked `rotated_at`. Replaying a rotated token revokes the
  **whole family**, its newest token included, so the owner must sign in again; the user's other
  sessions are untouched. A revoked-but-not-rotated token is just a 401. Each reuse writes a
  `SECURITY_AUDIT refresh_token_reuse` line naming the user and session ids, never the token.
- **Two tabs are not a theft.** A replay within `REFRESH_REUSE_GRACE_SECONDS` (default 5, 0 turns
  it off) of the rotation, while the family is live, gets a fresh access token and no refresh
  cookie, and revokes nothing (see *Design notes*).
- **Sessions are families.** The list shows one entry per family with a stable id; revoking it
  revokes the family; sign-out revokes the current family server-side; "sign out everywhere else"
  and a password change keep the current family.
- **CSRF, three layers:** `SameSite=Lax` cookies; Fetch Metadata (a browser-labelled
  `Sec-Fetch-Site: cross-site` unsafe request is refused, which also closes login CSRF); and a
  **signed double-submit token bound to the session**, required on every write that carries the
  access cookie, compared in constant time. The CSRF cookie now lives as long as the refresh
  cookie.
- **Single-use reset links:** a keyed fingerprint of the password the link resets rides in it, so
  the link stops matching once used.

## Design notes

**Why the family, and not all sessions or one token.** Rotation means a stolen refresh token and
the real one cannot both stay valid: whoever refreshes second presents a spent token. The server
cannot tell which of the two is the thief, so it must end the whole chain (RFC 9700, the OAuth 2.0
Security BCP, §4.14.2). Ending only the presented token leaves the thief's successor alive; ending
every session the user has, as `main` did, punishes the user's other devices for nothing and turns
the sessions list's revoke button into a self-sign-out.

**The grace window is a deliberate trade.** `session-refresh.js` de-duplicates refreshes inside one
page, but two tabs share the cookie jar and not the JavaScript: when both are polling as the access
cookie expires, the second tab's refresh can leave before the first one's response lands, and it
carries the token the first just spent. Without a window that is a receptionist signed out mid-shift.
With it, a thief who replays a stolen token within 5 seconds of the owner's rotation gets one access
token (15 minutes, no refresh token) and is not detected then; the owner's next rotation after the
window still is (the owner keeps the spent token until their own jar updates). Auth0 and Okta ship
the same window; it is a setting, and 0 turns it off.

**Why sign the CSRF token.** A plain double-submit token only proves that the request could write
the cookie and the field. Anything that can plant a cookie for the site (a compromised sibling
subdomain, a network attacker on plain HTTP) can make both match, which is why OWASP recommends the
signed variant. The token is `nonce.HMAC(secret, session id, nonce)`, bound to `sid`, the token
family (now carried by the access token). A token minted for another session fails. A request with
only the refresh cookie (the access cookie expired) can reach nothing but refresh and sign-out, and
those two routes check the binding against the refresh token's own family. A browser signed in
before this change has no bound CSRF cookie yet; its next refresh issues one (see *Risk*).

**Expand, not contract.** `family_id` and `rotated_at` are nullable and existing rows are backfilled
as families of one, so the previous release, which writes rows without them, keeps working during a
deploy and after a rollback (`docs/CICD/RUNBOOK_DEPLOY.md`). A later release can make `family_id`
`NOT NULL`. One trap found on the way: `NOT (family_id = :x OR …)` is NULL, not true, for a row
whose `family_id` is NULL, so "sign out everywhere else" skipped pre-migration rows; the negation is
now `coalesce(family_id, id) != :x` (the existing password-change test caught it).

**Out of scope:** staff invitations (Issue 22); patient OTP (Issue 17). The email-OTP verify
endpoint has no attempt limit today; Issue 17 puts the attempt lock in the shared OTP store, which
covers it.

## Changes

- **`alembic/versions/0002_refresh_token_family.py`** (new), **`src/database/models/refresh_token.py`:**
  `family_id` (indexed), `rotated_at`, and `session_id` (the family, or the row's own id).
- **`src/api/v1/routes/auth.py`:** families at sign-in and rotation; the replay, grace and revoked
  branches in `/auth/refresh`; sign-out, the sessions list and both revocations by family; the CSRF
  cookie minted per session with the refresh lifetime; single-use reset links.
- **`src/core/refresh_token_policy.py`:** `in_family`, `outside_family`, `revoke_family`,
  `revoke_families_except`, `family_is_live`.
- **`src/core/csrf_middleware.py`:** rewritten: Fetch Metadata, `mint_csrf_token`,
  `csrf_token_is_bound`, the signed and bound check.
- **`src/core/security.py`:** the `sid` claim, `session_id_from_access_token`,
  `password_fingerprint` and the `pwv` claim.
- **`src/core/config.py`, `.env.example`:** `REFRESH_REUSE_GRACE_SECONDS`.
- **`src/commons/enums.py`:** `SecurityAuditEvent.REFRESH_TOKEN_REUSE`.
- **`tests/integration/auth/test_auth_signin_sessions.py`:** the reuse test rewritten for families;
  13 new cases: the family replay, the two-tab race, a signed-out token, a list id surviving
  rotation, another user's session id, sign-out of every token in the session, cookie flags in
  staging and production, the missing, foreign and cross-site CSRF cases, a silent refresh after the
  access cookie expired, and a reset link used twice.

## Testing

- [x] `ruff check`, `ruff format --check` and `mypy src/` clean.
- [x] `make test`: **1062 passed**, 17 skipped, 9 xfailed. `pytest tests/integration/auth`: 35 in the
      sessions file, all passing. PostgreSQL and Redis tests: **17 passed**, including the migration
      round trip (`downgrade base` → `upgrade head`) and Alembic's "autogenerate finds nothing".
- [x] **The probe above**, the same script on `main` and on this branch.
- [x] **How to verify, on a real server** (`ENVIRONMENT=staging`, PostgreSQL, the demo nurse):

```text
$ make migrate-up          # a database with 2 refresh rows from before
Running upgrade 0001 -> 0002, refresh_token: a family per sign-in, and when a token was rotated
 id       | family_id | rotated_at | revoked
 84a1f43b | 84a1f43b  |            | f          ← backfilled: each old row is its own family
 b289df73 | b289df73  |            | f

1. Cookie flags (POST /api/v1/auth/password/login -> 200):
set-cookie: bk_clinicq_refresh_token=<value>; HttpOnly; Max-Age=604800; Path=/; SameSite=lax; Secure
set-cookie: bk_clinicq_access_token=<value>; HttpOnly; Max-Age=900; Path=/; SameSite=lax; Secure
set-cookie: bk_clinicq_csrf=<value>; Max-Age=604800; Path=/; SameSite=lax; Secure

2. Replay (device A and device B signed in):
A refresh (R1 -> R2)            200
replay R1 within 5 s (2 tabs)   200  (fresh access token, nothing revoked)
replay R1 after 6 s (theft)     401
A refresh with R2 (newest)      401  (family revoked: sign in again)
B refresh (other session)       200
   session    | rows | live | rotated
 e3e3d4f2fd7b |    2 |    0 |       1      ← A's family: every row revoked
 40f3f59fc817 |    2 |    1 |       1      ← B: untouched, rotated normally
SECURITY_AUDIT refresh_token_reuse outcome=failure user_id=86b00345-… session=01a09256-…-e3e3d4f2fd7b revoked_rows=1

3. CSRF on B's cookie session:
PATCH /auth/me, no token              403 {"detail":"Invalid or missing CSRF token"}
PATCH /auth/me, its own token         200
PATCH /auth/me, A's token planted     403
POST login, Sec-Fetch-Site: cross-site 403

4. Twelve wrong passwords for one account: 401 ×7, then 429 ×5
   (per-account budget 10 per 15 minutes; the three sign-ins above counted too)
```

- [ ] Not shown: a screenshot. No template, stylesheet or script changed; `session-refresh.js` and
      `csrf-htmx.js` work unchanged (they echo the cookie, whatever its format).

## Acceptance criteria

- [x] A successful sign-in sets the refresh cookie with `HttpOnly`, `Secure` and `SameSite`: read
      off a real staging response above, and a test on staging and production responses. In
      development `Secure` is off so `http://localhost` works.
- [x] Presenting an already-used refresh token revokes the whole family and forces
      re-authentication: the owner's newest token, never replayed, is refused afterwards; the other
      session survives. Shown on the server and in
      `test_replaying_a_rotated_refresh_token_revokes_its_whole_family`.
- [x] A state-changing request without a valid CSRF token is rejected with 403: no token, a missing
      CSRF cookie, another session's token, a cross-site request.
- [x] Sign-out invalidates the refresh token server-side: the session's whole family, so both the
      current cookie and an earlier copy are refused afterwards
      (`test_logout_is_server_side_for_every_token_of_the_session`).
- [x] Failed sign-ins are rate limited per account and per IP: shown above;
      `tests/integration/auth/test_password_login_rate_limit.py` covers both budgets.
- [x] A staff member can see and revoke their own active sessions: one entry per session, a stable
      id, 404 for anyone else's.

## Risk and rollback

**Migration `0002`** adds two nullable columns and an index to `refresh_token` and backfills
`family_id`; it is fast (one short table) and reversible (`downgrade` drops them). The previous
release runs on the new schema. Nobody is signed out by the deploy: an access token minted before it
has no `sid`, so the CSRF check falls back to the plain match for it, and the refresh route accepts
a missing or unsigned (pre-deploy) CSRF cookie once and mints a bound one. A reset link issued
before the deploy has no fingerprint and is refused; request a new one. Rollback is a revert (the
columns can stay).

**Follow-ups noticed:** `session-refresh.js` wraps `fetch` but htmx uses `XMLHttpRequest`, so an
htmx request that 401s after the access cookie expires is not silently refreshed (worth an issue
for the dashboard, M7). The email-OTP attempt lock lands with Issue 17.

Closes #16

🤖 Generated with [Claude Code](https://claude.com/claude-code)
