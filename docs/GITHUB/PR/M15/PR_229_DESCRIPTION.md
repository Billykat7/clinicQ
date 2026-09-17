# PR: The CSRF check no longer locks a browser out of its own sign-in (Issue 229 / M15-229)

**Milestone:** [Milestone 15: Clinic Onboarding & Patient Sign-in](https://github.com/Billykat7/clinicQ/milestone/15) ·
**Issue:** [#229](https://github.com/Billykat7/clinicQ/issues/229) · **Builds on:** #16 (the signed
double-submit token), #17 (the patient session that shares its cookie), both merged

`Invalid or missing CSRF token` has two causes that are not attacks — they are the check refusing the
person using it, permanently, with nothing in the UI that clears it. Both were found by using the app.

## 1. One browser, two sessions, one cookie

A staff sign-in and a patient sign-in both write **the same** `bk_clinicq_csrf` cookie
(`_set_auth_session_cookies` and `start_session`), with tokens bound to **different** session ids.
The middleware resolved exactly one session to check against, and preferred the staff one:

```python
# before — src/core/csrf_middleware.py
access = request.cookies.get(settings.access_token_cookie_name) or request.cookies.get(
    settings.patient_session_cookie_name
)
...
session_id = session_id_from_access_token(access)
if session_id is not None and not csrf_token_is_bound(submitted, session_id):
    return _forbidden("Invalid or missing CSRF token")
```

So the moment someone signed into the dashboard also signs in as a patient — which is what anyone
demoing or testing M15's patient sign-in does — the shared cookie holds a patient-bound token, the
staff session is what gets checked, and **every staff write fails** for the next 25 minutes. Signing
out and back in does not help: the patient cookie is still there.

The same script against `main` and against this branch, on a live server:

```
--- BEFORE (main) ---                          --- AFTER (this branch) ---
1) staff signs in                              1) staff signs in
2) the same browser signs in as a patient      2) the same browser signs in as a patient
3) staff presses anything on the dashboard     3) staff presses anything on the dashboard
   PATCH /api/v1/auth/me -> 403                   PATCH /api/v1/auth/me -> 200
4) a token bound to neither session            4) a token bound to neither session
   PATCH /api/v1/auth/me -> 403                   PATCH /api/v1/auth/me -> 403
```

Line 4 is the point: **this is not a loosening.** The binding is now checked against *every* session
the request carries, and both session cookies are `httpOnly` and set only by this server — so a
session id the request can prove is one the browser genuinely holds. A token minted for a third
session, which is precisely what an attacker on a sibling subdomain can plant, is still refused.

## 2. A leftover CSRF cookie refused the sign-in that would replace it

With no session cookie on the request, the middleware still demanded the echo whenever a CSRF cookie
happened to be in the jar. Sign-in, sign-up and "send me a reset link" carry no ambient credential
for a token to protect — the module's own docstring says layers 1 and 2, `SameSite=Lax` and Fetch
Metadata, are what stop login CSRF. Requiring it bought nothing and cost everything: any cookie the
page could not read back made **signing in itself** impossible.

```
BEFORE  stale csrf cookie in the jar, no session, POST /auth/password/login
        {"detail":"Invalid or missing CSRF token"}  <- 403
AFTER   {"access_token":"…","token_type":"bearer","email":"manager@clinicq.example"}  <- 200
```

A **refresh** cookie is still a session, so `/auth/refresh` and `/auth/logout` keep the echo and keep
their own family-binding check. A cross-site sign-in with that same stale cookie is still a 403 —
Fetch Metadata is untouched.

## 3. `SESSION_COOKIE_SECURE`

`secure=not settings.is_development` is right for every environment that terminates TLS and silently
wrong for one that does not: a browser **discards** a `Secure` cookie served over plain `http://`, so
a host reachable only as `http://host:port` signs a person in, sets three cookies the browser throws
away, and behaves for the rest of the session as though they never signed in — no error, no cookie,
no clue. The flag is now an explicit setting instead of an inference, unset meaning exactly today's
behaviour. `localhost` is exempt from the rule in every current browser, so local work never needs it.

## Summary

- **`src/core/csrf_middleware.py`** — `_session_ids(request)` returns every session the request is
  authenticated as (staff, then patient); the binding check accepts a token bound to any of them. The
  unauthenticated branch lets a request with no session **and no refresh cookie** through whatever is
  in the CSRF cookie. The module docstring records both, and why.
- **`src/core/config.py`** — `session_cookie_secure: bool | None` and the `session_cookies_secure`
  property ("the override when set, else *not development*").
- **`src/api/v1/routes/auth.py`, `src/modules/patients/sessions.py`** — the four cookie setters read
  the property instead of re-deriving it from `ENVIRONMENT`.
- **`.env.example`** — regenerated (179 settings).
- **`docs/OPS/SESSION_COOKIES.md`** — what a signed-in browser holds, the two-session case, why
  sign-in is never refused for a token, and a four-step "when someone reports this" list.

## Not done here, and not claimed

- **The patient session keeps sharing the CSRF cookie name.** Splitting it would also work and costs
  a settings key, a template variable and a rule about which pages read which cookie; accepting
  either of the browser's *own* sessions is the smaller change and loses nothing.
- **No change to `SameSite`, to the Fetch Metadata layer, or to which paths are protected.**
- **No UI change.** The sign-in modal is still a modal — [Issue 231](https://github.com/Billykat7/clinicQ/issues/231)
  moves it onto real pages, and it must not inherit this lockout.

## Verification

- [x] `TZ=UTC pytest tests/integration/auth` — **94 passed**, including the eight new Issue 229
  tests and every Issue 16 CSRF test unchanged.
- [x] `TZ=UTC pytest tests/integration/patients tests/integration/security tests/unit` — **1481
  passed**, 3 skipped, 9 xfailed.
- [x] Live, against a seeded local server: the before/after runs above.
- [x] `ruff check`, `ruff format --check`, `mypy src/` (335 files) clean.
- [x] `python scripts/update_milestone_progress.py --assume-closed 229`.

## Acceptance criteria

- [x] **A browser holding a staff session and a patient session writes on both sides**, whichever
  sign-in happened last — `test_signing_in_as_a_patient_leaves_staff_writes_working`,
  `test_the_staff_token_still_works_while_a_patient_session_is_open`.
- [x] **A token bound to a session the request does not carry is still refused** —
  `test_a_token_bound_to_neither_session_is_still_refused`, and the Issue 16
  `test_a_csrf_token_from_another_session_is_refused` unchanged.
- [x] **A leftover CSRF cookie does not refuse a sign-in** —
  `test_a_leftover_csrf_cookie_does_not_block_signing_in`.
- [x] **A cross-site sign-in is still refused with that cookie present** —
  `test_a_cross_site_sign_in_is_still_refused_with_a_leftover_cookie`.
- [x] **`/auth/refresh` without an echoed token is still a 403** —
  `test_a_leftover_csrf_cookie_still_guards_refresh`.
- [x] **`SESSION_COOKIE_SECURE=false` serves cookies a plain-`http://` browser keeps; unset is
  unchanged** — the two `session_cookie_secure` tests, plus the existing
  `test_sign_in_sets_httponly_secure_samesite_cookies_outside_development`.

## Risk and rollback

- **No migration, no data change, no new cookie.** A browser that is working today keeps working:
  its token is bound to the session it already holds, which is still in the set being checked.
- **`SESSION_COOKIE_SECURE` is unset by default**, so no environment changes behaviour on deploy.
- **Rollback:** revert. Browsers holding two sessions go back to failing until a cookie expires.

Closes #229
