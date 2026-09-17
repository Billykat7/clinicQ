# Issue 229: The CSRF check that locks a browser out of signing in

> **In short:** Two ways a browser earns a permanent `Invalid or missing CSRF token` that it cannot clear from the UI: holding a staff **and** a patient session at once, and holding a CSRF cookie the page cannot echo. Neither is an attack; both are the check refusing its own user.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 16 (weeks 31–32) |
| **Owner** | A, Backend Lead (backup: E, DevOps/QA) |
| **Area** | Backend / Security |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 16](../M3/ISSUE_16_staff_signin_sessions_csrf.md): the signed double-submit token<br>[Issue 17](../M3/ISSUE_17_patient_identity_otp.md): the patient session that shares its cookie |
| **Unblocks** | [Issue 231](ISSUE_231_auth_pages_not_modal.md): the sign-in pages, which must not inherit the lockout |

## Context

`src/core/csrf_middleware.py` mints a token bound to the session — `<nonce>.<HMAC(secret, sid,
nonce)>` — and refuses an unsafe `/api/*` request whose echoed token is not bound to the session on
the request. That is the right shape, and it has two blind spots that turn it against the person
using it. Both were found by using the app, not by reading it.

**One browser, two sessions, one cookie.** A staff sign-in
(`_set_auth_session_cookies`) and a patient sign-in (`start_session`, Issue 17) both write
`settings.csrf_cookie_name` — the *same* cookie — with tokens bound to *different* session ids. The
middleware then resolved exactly one session to check against, and preferred the staff one:

```python
# before
access = request.cookies.get(settings.access_token_cookie_name) or request.cookies.get(
    settings.patient_session_cookie_name
)
...
session_id = session_id_from_access_token(access)
if session_id is not None and not csrf_token_is_bound(submitted, session_id):
    return _forbidden("Invalid or missing CSRF token")
```

So the moment a person signed into the dashboard also signs in as a patient — which is what anyone
demoing or testing M15's patient sign-in does — the shared cookie holds a patient-bound token, the
staff session is what gets checked, and **every staff write fails** until one of the cookies expires
25 minutes or a week later. Signing out and back in does not help: the patient cookie is still
there. Nothing in the UI says which cookie to clear.

**A leftover CSRF cookie refuses the sign-in that would replace it.** With no session cookie on the
request, the middleware still demanded the echo whenever a CSRF cookie happened to be present:

```python
# before
if csrf_cookie is None:
    return await call_next(request)
submitted = await _submitted_token(request)
if submitted is not None and hmac.compare_digest(submitted, csrf_cookie):
    return await call_next(request)
return _forbidden("Invalid or missing CSRF token")
```

Sign-in, sign-up and "send me a reset link" carry no ambient credential for a token to protect —
the module's own docstring says so: layers 1 and 2, `SameSite=Lax` and Fetch Metadata, are what stop
login CSRF. Requiring the echo here buys nothing and costs everything: any cookie the page cannot
read back — a renamed `CSRF_COOKIE_NAME`, a duplicate left by an earlier `Domain=`, a helper script
that did not load, a second cookie at a different path — makes **signing in itself** impossible,
with no route back that does not involve opening devtools.

**And the flag that drops every cookie.** `secure=not settings.is_development` is right for every
environment that terminates TLS and silently wrong for one that does not: a browser discards a
`Secure` cookie served over plain `http://`, so a staging or production host reachable only as
`http://host:port` signs a person in and then behaves as though they never signed in. That is a
third way to spend an afternoon on a cookie, so it gets an explicit, recorded setting rather than an
inference from `ENVIRONMENT`.

## Starting point

- `src/core/csrf_middleware.py`: `mint_csrf_token`, `csrf_token_is_bound`, `CsrfProtectMiddleware.dispatch`.
- `src/core/security.py`: `session_id_from_access_token`, which already understands **both** token
  types (a staff `access` token and a `patient` session) and verifies the signature and the type.
- `src/api/v1/routes/auth.py`: `_set_refresh_token_cookie`, `_set_access_token_cookie`, `_set_csrf_cookie`.
- `src/modules/patients/sessions.py`: `start_session`, which writes the shared CSRF cookie.
- `tests/integration/auth/test_auth_signin_sessions.py`: the Issue 16 CSRF tests to keep green.

## Scope

- Check the binding against **every** session the request carries, staff and patient
  (`_session_ids`). Both session cookies are `httpOnly` and only this server sets them, so a session
  id the request can prove is one the browser genuinely holds — an attacker can plant a CSRF cookie,
  never a session cookie. A token bound to a third session is still refused.
- Let a request with **no session cookie and no refresh cookie** through whatever is in the CSRF
  cookie. A refresh cookie is still a session, so `/auth/refresh` and `/auth/logout` keep the echo
  and keep their own family-binding check (`_refresh_csrf_ok`).
- `SESSION_COOKIE_SECURE`: an explicit override for the `Secure` flag on the session, refresh and
  CSRF cookies, unset meaning today's "Secure outside development".
- Say all of this in the module docstring, so the next reader does not have to rediscover it.

## Out of scope

- Splitting the patient session onto its own CSRF cookie name. It would work, and it costs a
  settings key, a template variable and a rule about which pages read which cookie; accepting either
  of the browser's own sessions is the smaller change and loses nothing.
- Any change to `SameSite`, to the Fetch Metadata layer, or to which paths are protected.
- The sign-in UI itself — [Issue 231](ISSUE_231_auth_pages_not_modal.md) moves it onto real pages.

## Acceptance criteria

- [ ] A browser holding a staff session **and** a patient session can write on both sides, whichever
      sign-in happened last
- [ ] A token bound to a session the request does **not** carry is still refused, on both sides
- [ ] A leftover CSRF cookie with no session behind it does not refuse a sign-in, a sign-up or a
      reset-link request
- [ ] A cross-site sign-in is still refused with that same leftover cookie present (Fetch Metadata
      is untouched)
- [ ] `/auth/refresh` with a refresh cookie and no echoed token is still a 403
- [ ] `SESSION_COOKIE_SECURE=false` serves cookies a plain-`http://` browser keeps; unset behaves
      exactly as today; everything else about the cookies is unchanged
- [ ] Every Issue 16 CSRF test passes unchanged

## How to verify

1. `TZ=UTC pytest tests/integration/auth tests/integration/patients tests/integration/security tests/unit`
2. Against a running server, `scripts/…/two_sessions.sh` in the pull request: staff signs in, the
   same browser signs in as a patient, a staff write returns 200, and a planted token returns 403.
3. `make check`

## Files touched

- `src/core/csrf_middleware.py`, `src/core/config.py`
- `src/api/v1/routes/auth.py`, `src/modules/patients/sessions.py`
- `.env.example` (regenerated), `docs/OPS/SESSION_COOKIES.md`
- `tests/integration/auth/test_auth_signin_sessions.py`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #229
