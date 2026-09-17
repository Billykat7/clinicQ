# PR: Signing in is four pages, not a dialog over the page you were on (Issue 231 / M15-231)

**Milestone:** [Milestone 15: Clinic Onboarding & Patient Sign-in](https://github.com/Billykat7/clinicQ/milestone/15) ·
**Issue:** [#231](https://github.com/Billykat7/clinicQ/issues/231) · **Builds on:** #16 (the sign-in
API), #229 (the CSRF lockout these pages must not inherit)

`partials/login_modal.html` was one `<div>` holding **four** screens — sign in, the one-time code,
sign up, choose a new password — swapped by 561 lines of `login-modal.js`, and included on the home
page, the marketing and legal pages, `/register-clinic` and the whole dashboard shell.

**It had no address.** Nothing could link to signing in. Nobody could bookmark it, reload it or open
it in a second tab; Back did not leave it; support could not say "go to this page". The redirect
from a protected page had to say so out loud — the *home page*, with a query parameter asking a
dialog to open itself:

```python
return RedirectResponse(url=f"/?next={quote(next_path, safe='')}&openSignin=1", status_code=302)
```

**The reset email was worse.** It landed on the home page too, with the one-time token in the
address bar, and the modal's first act was to take it back out:

```python
reset_link = f"{base_url}/?resetToken={quote(token, safe='')}"   # before
```
```javascript
url.searchParams.delete("resetToken");                            // and then hide it again
window.history.replaceState({}, document.title, url.pathname + url.search + url.hash);
```

**And what it showed arrived late.** Which controls existed came from a `GET /auth/config` *after
first paint*, so on a slow connection a person watched a password field appear and vanish. The
server knows all three answers before it renders a byte.

## The four pages

| `/signin` | `/signin` after a session ended | the code step, in place |
|---|---|---|
| ![The sign-in card: email, password, Sign in, Email me a code instead, and links to Forgot your password and Create an account](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/signin.png) | ![The same card with a green notice reading "Your session ended. Sign in again to carry on."](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/signin-expired.png) | ![The card now asking for the code sent to manager@clinicq.example, with Verify and sign in and Send another code](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/signin-code-step.png) |

| `/signup` | `/forgot-password` | `/reset-password?token=…` | `/signin`, light |
|---|---|---|---|
| ![Create your BK ClinicQ account: one email field and Create account](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/signup.png) | ![Reset your password: one email field and Email me a reset link](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/forgot-password.png) | ![Choose a new password: two password fields and Set new password](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/reset-password.png) | ![The sign-in card in the light theme](https://raw.githubusercontent.com/Billykat7/clinicQ/4148d2d829316f9bbe5776f591f0a40d0b32fbe9/docs/GITHUB/PR/M15/assets/pr231/signin-light.png) |

They wear the front door's clothes — `landing.css`, the shared `lp_header` / `lp_footer`, like
`/register-clinic` — because that is where someone signing in came from, and the header and footer
are the way back out.

## Three things the pages do that the modal could not

1. **The server shapes the page.** `otp_login_enabled`, `password_login_enabled` and
   `signup_enabled` are rendered into the template. Nothing is fetched after paint, and nothing
   appears or vanishes. A page that *cannot* work is a 404: `/signup` with sign-up off,
   `/forgot-password` and `/reset-password` with password sign-in off — rather than a form whose
   only possible outcome is the API's 403.
2. **`next` is a way back, never a way off the site.** `_safe_next` keeps a same-origin path and
   turns anything else into `/dashboard`; the page's script checks the attribute again, so a
   tampered DOM cannot make an open redirect out of a successful sign-in.
3. **Why you are here is written by the server.** `?expired=1`, `?reset=1` and `?signedout=1` are
   flags that *choose* one of three fixed sentences in the route. No text from a URL reaches the
   page, so a crafted link cannot put words on a sign-in screen.

## The one step that is not its own page

The **code step stays on `/signin`**, replacing the email step in place. The code belongs to the
email just typed, and a fresh `GET` would have nothing to attach it to; the address stays `/signin`
and a reload honestly starts over. It is a page, not a dialog — full width, in the flow, with the
header and footer around it.

## What happens when a session ends mid-task

The modal existed for a reason the pages have to answer: the dashboard's outbox (Issue 55) and the
walk-in form (Issue 51) call `window.BKPAuth.reauthenticate()` when an action comes back 401, so
unsent work is not lost. `reauthenticate()` now goes to `/signin?expired=1&next=<this page>`, and
**the trip keeps the work**:

- the **outbox** already kept its queue in this tab's `sessionStorage`; it now also marks a held
  action ready to send again on the way back, and does not raise the "you have unsent work" prompt
  on the way there — which would otherwise have stopped the redirect at a browser dialog;
- the **walk-in form** writes what was typed, and its idempotency key, before it leaves, and puts
  them back on return, so pressing Enter asks about the *same* walk-in rather than issuing a second
  ticket. It says so on the page. That is better than the previous fallback, which reloaded and lost
  the form whenever `BKPAuth` was not present.

The end-to-end browser test for that journey follows the new route: click → `/signin?expired=1` →
sign in → back on the board → `Done: Start for T001`.

## Summary

- **New:** `layouts/auth.html` (a fourth layout, so every page still reaches one),
  `web/auth_{signin,signup,forgot,reset}.html`, `static/js/auth-pages.js`, the `.lp-auth-*` block in
  `landing.css`, and four routes in `src/web/routes.py` with `_safe_next` and `_SIGN_IN_NOTICES`.
- **Gone:** `partials/login_modal.html` (165 lines) and `static/js/login-modal.js` (561), the
  `?openSignin=1` redirect, the `?resetToken=` landing and the `history.replaceState` that hid it.
- **Moved:** the signed-in shell's `/me` render, user menu, avatar and sign-out are
  `static/js/account-shell.js`.
- **Repointed:** every `data-open-signin` button is an `<a href="/signin">`; `_redirect_to_sign_in`
  and `reset_link` point at the new pages.
- **Guards:** the four public pages are added to `_UNGATED_PAGES` with the reason each needs no
  grant, and `layouts/auth.html` to the shell test's `LAYOUTS`.

### One guard had to be decided rather than fixed

`test_at_least_one_style_element_exists_so_the_nonce_guard_is_not_vacuous` existed because the
modal's `<style nonce="…">` was the **only** `<style>` element left in the templates, and its
docstring said: *"If it is ever removed, this test fails and someone decides deliberately whether
the nonce path still needs covering."* This PR removes it. The decision: **keep the rule** — the
next person to add a `<style>` needs it — and check the thing it depends on instead of its last
user. The test now asserts the nonce is still minted fresh per request and still named in the CSP,
so a `<style nonce="…">` added tomorrow would work.

## Not done here, and not claimed

- **The patient's sign-in at `/t/` is untouched** (Issues 200, 219). It was already a page.
- **No change to the auth API**: the same six endpoints, the same bodies, the same answers,
  the same rate limits and the same no-enumeration behaviour.
- **No OAuth, no "remember me", no password field on `/signup`** — activation still chooses it.

## Verification

- [x] `TZ=UTC pytest tests/integration tests/unit` green, including 22 new tests in
  `tests/integration/public/test_auth_pages.py`.
- [x] `TZ=UTC pytest tests/integration/auth` — 94 passed; the reset test now also asserts the
  emailed link's **path**, so a reset that lands anywhere else fails there.
- [x] By hand on a seeded local server: password sign-in from `/signin` → the dashboard; the code
  step → `/dev/outbox` → the dashboard; `/dashboard` while signed out → `302 /signin?next=%2Fdashboard`.
- [x] `?next=//evil.example/pwn` renders `data-next="/dashboard"`;
  `?expired=<script>alert(1)</script>` renders the fixed sentence.
- [x] `ruff check`, `ruff format --check`, `mypy src/` (335 files) clean.
- [x] `python scripts/update_milestone_progress.py --assume-closed 229,230,231`.

## Acceptance criteria

- [x] **Each step has an address**, reloads and can be linked to — four 200s, tested.
- [x] **Password and code sign-in both work from `/signin`** and land on `next`.
- [x] **A signed-out visit redirects to `/signin?next=<page>`** and lands back on it.
- [x] **`next` that is not a same-origin path becomes `/dashboard`** — six cases, tested.
- [x] **The reset email links to `/reset-password?token=…`**, and a completed reset ends on
  `/signin?reset=1` saying so.
- [x] **A page that cannot work is a 404** — `/signup`, `/forgot-password`, `/reset-password`.
- [x] **All four render when the database is down**, like the rest of the front door.
- [x] **A session that ends mid-action** sends the person to `/signin` and the held action is sent
  when they come back — the e2e journey.
- [x] **No `login_modal.html`, no `login-modal.js`, no `openSignin`, no `resetToken`** anywhere.

## Risk and rollback

- **Anyone holding an old reset email** has a `/?resetToken=…` link that no longer opens anything.
  Those links expire in `PASSWORD_RESET_LINK_EXPIRE_HOURS`, and asking for a new one is one page.
- **A bookmark to `/?openSignin=1`** now opens the home page and does nothing else; the "Sign in"
  button on it is a link to `/signin`.
- **Rollback:** revert. Nothing is stored differently and no migration is involved.

Closes #231
