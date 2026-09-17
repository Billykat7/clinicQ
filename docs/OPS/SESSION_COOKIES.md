# Session cookies: what the browser holds, and how it gets stuck

Everything a signed-in browser carries, why a person sometimes sees **"Invalid or missing CSRF
token"**, and the one setting that decides whether the cookies survive the connection at all.

Code: [`src/core/csrf_middleware.py`](../../src/core/csrf_middleware.py),
[`src/api/v1/routes/auth.py`](../../src/api/v1/routes/auth.py),
[`src/modules/patients/sessions.py`](../../src/modules/patients/sessions.py).

## The cookies

| Cookie (default name) | Set by | httpOnly | Lifetime | Carries |
|---|---|---|---|---|
| `bk_clinicq_access_token` | staff sign-in, refresh | yes | 25 min | the staff access JWT, with the session id (`sid`) |
| `bk_clinicq_refresh_token` | staff sign-in, refresh | yes | 7 days | the opaque refresh token; names the session family |
| `bk_clinicq_patient_session` | patient sign-in (OTP) | yes | `PATIENT_SESSION_HOURS` | the patient session JWT, with its own `sid` |
| `bk_clinicq_csrf` | **both** sign-ins, and every refresh | no | as long as the session it belongs to | `<nonce>.<HMAC(secret, sid, nonce)>` — the double-submit token |

All four are `SameSite=Lax`, `Path=/`, and `Secure` according to
[`SESSION_COOKIE_SECURE`](#session_cookie_secure) below. The CSRF cookie is deliberately readable by
the page: `src/static/js/csrf-htmx.js` mirrors it into `X-CSRF-Token` on every unsafe request, and
reads its name from `<meta name="bkp-csrf-cookie">` in `base.html`.

## One browser can hold two sessions

Staff and patients are different identities with different routes, and **one browser can be signed
in as both** — which is exactly what happens when someone demos the patient pages from the machine
they run the dashboard on. They share the `bk_clinicq_csrf` cookie, so whichever signed in last owns
its value.

Since Issue 229 the middleware checks the echoed token against **every** session on the request, not
just the staff one, so either token is accepted while both cookies are there. This is not a
loosening: both session cookies are `httpOnly` and only this server sets them, so a session id the
request can prove is one the browser genuinely holds. A token minted for a *third* session — what
an attacker on a sibling subdomain can plant — is still refused.

Before that fix, signing in as a patient made every staff write answer "Invalid or missing CSRF
token" until a cookie expired, and signing out and back in did not clear it.

## Signing in is never refused for a token

Sign-in, sign-up and "send me a reset link" carry no ambient credential, so there is nothing for a
double-submit token to protect there. `SameSite=Lax` and the Fetch Metadata check
(`Sec-Fetch-Site: cross-site` is refused outright) are what stop login CSRF, and they still do.

A request with **no session cookie and no refresh cookie** therefore goes through whatever is in the
CSRF cookie. Before Issue 229 a leftover cookie the page could not echo — a renamed
`CSRF_COOKIE_NAME`, a duplicate left behind by an earlier `Domain=`, a helper script that did not
load — made signing in itself impossible, with no way out that did not involve devtools.

A **refresh** cookie is still a session: `/auth/refresh` and `/auth/logout` still require the echo,
and still check the binding against the refresh token's own family.

## `SESSION_COOKIE_SECURE`

| Value | Effect |
|---|---|
| unset (default) | `Secure` everywhere except `ENVIRONMENT=development` |
| `true` | always `Secure` — a development instance sitting behind TLS |
| `false` | never `Secure` — a host that is only reachable over plain `http://` |

A browser **silently discards** a `Secure` cookie served over plain `http://`. A staging or
production instance reachable only as `http://host:port` therefore signs a person in, sets three
cookies the browser throws away, and behaves for the rest of the session as though they never signed
in — no error, no cookie, no clue. `localhost` is exempt from the rule in every current browser, so
local work never needs this.

Set `SESSION_COOKIE_SECURE=false` only on a host that genuinely has no TLS, and treat it as
temporary: session cookies in the clear are readable by anything on the path. The platform
deployment terminates TLS at nginx ([`docs/CICD/RUNBOOK_DEPLOY.md`](../CICD/RUNBOOK_DEPLOY.md)) and
needs no override.

## When someone reports "Invalid or missing CSRF token"

1. **Is there a `bk_clinicq_csrf` cookie at all?** No cookie on a signed-in browser means the
   `Secure` flag ate it — see the setting above — or something is clearing cookies.
2. **Is there more than one?** Two cookies of the same name at different `Domain`/`Path` scopes read
   differently in JavaScript and on the server. Clear the site's cookies once; the next sign-in sets
   one, host-only, at `/`.
3. **Did the page load `csrf-htmx.js`, and does it have the `bkp-csrf-cookie` meta?** Every template
   inherits both from `base.html`; a page that renders outside it will not send the header.
4. **Is the request cross-site?** That answers "Cross-site request refused", a different message.

---

**Navigation:** [Ops docs](.) · [Deploy runbook](../CICD/RUNBOOK_DEPLOY.md) · [Quickstart](../QUICKSTART.md)
