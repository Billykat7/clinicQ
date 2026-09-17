# Issue 231: Sign in, sign up and reset password on their own pages

> **In short:** Signing in happens in a modal laid over whatever page you were on. It has no address, so nothing can link to it, nobody can bookmark or reload it, Back does not work, and the password-reset email has to open the **home page** with the token in the address bar. Give each step a URL.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 16 (weeks 31–32) |
| **Owner** | D, Frontend/Clinic (backup: A, Backend Lead) |
| **Area** | Frontend / Auth |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 16](../M3/ISSUE_16_staff_signin_sessions_csrf.md): the sign-in API<br>[Issue 229](ISSUE_229_csrf_sign_in_lockout.md): the CSRF lockout the new pages must not inherit |
| **Unblocks** | Nothing in this milestone. |

## Context

`src/templates/partials/login_modal.html` is one `<div>` holding **four** screens — sign in, the
one-time code, sign up, choose a new password — swapped by `login-modal.js` with
`el.classList.toggle("active")`. It is included on the home page, the three legal and marketing
pages, `/register-clinic` and the whole dashboard shell, and it costs more than it looks.

**It has no address.** Nothing can link to signing in. Nobody can bookmark it, reload it, or open
it in a second tab. Back does not leave it. A support message cannot say "go to this page". The
redirect from a protected page has to say so out loud:

```python
# src/web/routes.py — before
return RedirectResponse(url=f"/?next={quote(next_path, safe='')}&openSignin=1", status_code=302)
```

— the **home page**, with a query parameter asking a dialog to open itself.

**The reset email is worse.** The link lands on the home page too, with the one-time token in the
address bar, and the modal's first act is to take it back out again:

```python
reset_link = f"{base_url}/?resetToken={quote(token, safe='')}"   # before
```
```javascript
url.searchParams.delete("resetToken");                            // and then hide it
window.history.replaceState({}, document.title, url.pathname + url.search + url.hash);
```

**And what it shows arrives late.** Which controls exist — the password field, the "send me a code"
button, the sign-up link — is decided by a `GET /auth/config` **after first paint**, so on a slow
connection a person watches a password field appear and vanish. The server knows all three answers
before it renders a byte.

## Starting point

- `src/templates/partials/login_modal.html` (165 lines, including a `<style nonce>` block) and
  `src/static/js/login-modal.js` (561 lines), of which the modal is most but not all: the header's
  `/me` render, the user menu, the avatar and sign-out belong to the signed-in shell and stay.
- `src/web/routes.py`: `_redirect_to_sign_in`, and the front-door pages' `public_page_context`.
- `src/api/v1/routes/auth.py`: `reset_link`.
- `src/static/css/landing.css`: `.lp-field`, `.lp-hint` — the front door's form styles, from
  `/register-clinic` (Issue 29).
- `window.BKPAuth.reauthenticate(message)`: the dashboard's outbox (Issue 55) and the walk-in form
  (Issue 51) call it when a session ends mid-task, and carry on when its promise resolves.

## Scope

- Four pages, on the front door, in a new `layouts/auth.html`: `/signin`, `/signup`,
  `/forgot-password`, `/reset-password`.
- **The server shapes the page.** `otp_login_enabled`, `password_login_enabled` and
  `signup_enabled` are rendered into the template; no `/auth/config` fetch, nothing appears or
  vanishes after paint. A page that cannot work (`/signup` with sign-up off) is a 404.
- The emailed reset link becomes `/reset-password?token=…`, and the token is a hidden field rather
  than something the home page has to carry and then hide.
- `_redirect_to_sign_in` points at `/signin?next=…`; `next` is checked to be a same-origin path
  (`_safe_next`) in the route **and** again in the page's script.
- Why someone is there is rendered by the server from a fixed set of sentences keyed by a query
  flag (`expired`, `reset`, `signedout`) — never text taken from the URL.
- The modal and `login-modal.js` are deleted. What the signed-in shell still needs moves to
  `account-shell.js`, and `reauthenticate()` becomes a trip to `/signin?expired=1&next=…`.
- **The trip must not lose work.** The outbox already keeps its queue in `sessionStorage`; it now
  also marks a held action ready to send again on the way back, and does not raise the "unsent
  work" prompt on the way there. The walk-in form saves what was typed, with its idempotency key.
- The code step stays on `/signin`, in place: the code belongs to the email just typed, and a fresh
  GET would have nothing to attach it to.

## Out of scope

- The patient's own sign-in at `/t/` (Issues 200, 219). It is already a page.
- Any change to the auth API: the same six endpoints, the same bodies, the same answers.
- OAuth, "remember me", or a password field on `/signup` — activation still chooses the password.
- Rate limits, lockouts and enumeration behaviour, all of which stay exactly where they are.

## Acceptance criteria

- [ ] `/signin`, `/signup`, `/forgot-password` and `/reset-password` each answer 200, reload, and
      can be linked to
- [ ] Signing in with a password, and with an emailed code, both work from `/signin` and land on
      `next`
- [ ] A signed-out visit to a protected page redirects to `/signin?next=<that page>` and lands back
      on it after signing in
- [ ] `next` that is not a same-origin path becomes `/dashboard` — no open redirect
- [ ] The reset email links to `/reset-password?token=…`, and setting the password ends on
      `/signin` saying so
- [ ] `/signup` is a 404 when sign-up is off; `/forgot-password` and `/reset-password` are 404s when
      password sign-in is off
- [ ] All four render when the database is down, like the rest of the front door
- [ ] A session that ends mid-action sends the person to `/signin` and the held action is sent when
      they come back
- [ ] No `login_modal.html`, no `login-modal.js`, no `openSignin`, no `resetToken` anywhere

## How to verify

1. `TZ=UTC pytest tests/integration/public/test_auth_pages.py tests/integration/auth tests/unit`
2. `TZ=UTC pytest tests/e2e/dashboard` — the expired-session journey, end to end in a browser
3. By hand on a local server: both sign-in methods, the reset email's link, and `?next=`
4. `make check`

## Files touched

- `src/templates/layouts/auth.html`, `src/templates/web/auth_{signin,signup,forgot,reset}.html`
- `src/templates/partials/login_modal.html` (deleted), and the six templates that included it
- `src/static/js/auth-pages.js` (new), `src/static/js/account-shell.js` (replaces `login-modal.js`)
- `src/static/js/dashboard-outbox.js`, `src/static/js/dashboard-walkin.js`
- `src/static/css/landing.css`
- `src/web/routes.py`, `src/api/v1/routes/auth.py`
- `scripts/lint_surface_gates.py`, and the tests that asserted the old shape

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #231
