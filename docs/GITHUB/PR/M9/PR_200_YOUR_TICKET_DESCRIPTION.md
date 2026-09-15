# PR: Put Your ticket on the front door, opening the app's start page (Issue 200 follow-up / M9-200)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#200](https://github.com/Billykat7/clinicQ/issues/200), closed by PR #201 · **Follows:** PR #201

Once PR #201 gave patients the join page, the front door still had no way back to a ticket. **Find a
clinic** leads to a new ticket, but nothing on the home page opens an existing one or the phone sign-in, and
the app installs only from the ticket page. This PR adds **Your ticket** to every front-door page. It opens
`/t/`, which shows the patient's open ticket, or the last one that browser followed, or the phone sign-in.

These commits were pushed to #201's branch after #201 had merged. They are brought onto `main` here.

## Scope

- **In:**
  - **Your ticket** in the front door's top bar and footer;
  - keeping the top bar on one line on a 320 px phone;
  - the testing guide.
- **Out:**
  - an install button on the home page: the app's manifest is served only on `/t/` pages, and the install
    offer stays on the patient's own ticket (Issue 69).

## Summary

- **Your ticket on every front-door page** (`/`, `/features`, `/privacy`, `/terms`, `/register-clinic`):
  - a quiet button beside **Sign in** in the top bar;
  - a link in the footer's Product list;
  - both open `/t/`.
- **The top bar stays on one line on a small phone.** Action labels never wrap. At 26rem and below the
  actions tighten and the brand keeps only its mark, with the name still read by screen readers.
- **The Issue 200 integration tests assert data, never HTML.** Four assertions in PR #201 read page text,
  which `docs/IDE/RULES/testing-strategy.mdc` rules out. They now check the page's context instead, and the
  markup is checked in the browser tests.

## Design notes

- **With Sign in, not with the page links.** The top bar hides its links below 60rem, which is every phone,
  so a link there would never reach a patient.
- **A link to `/t/`, not a new page.** `/t/` already decides where to go: a signed-in patient's open ticket, the
  last unfinished ticket this phone followed, or the sign-in. The front door only has to point at it.

## Changes

- **`src/templates/web/partials/lp_header.html`, `lp_footer.html`:** the button and the footer link.
- **`src/static/css/landing.css`:** no wrapping in the actions; a tighter bar at 26rem and below.
- **`tests/e2e/patient/test_join_page.py`:** `test_the_home_pages_your_ticket_fits_a_320_px_phone_and_opens_the_app_start_page`;
  `_shot` can take the visible screen only.
- **`tests/integration/discovery/test_join_page.py`:** the HTML assertions replaced by the page's data.
- **`docs/OPS/PATIENT_APP_TESTING.md`:** section 0 names Your ticket.

## Testing

On this branch, rebuilt from `main` after PR #202 (`TZ=UTC`, PostgreSQL). The Issue 200 integration and browser
tests and the public front-door tests, then the unit tests:

```text
21 passed in 33.84s
1222 passed, 3 xfailed in 25.10s
```

The new browser test:

1. opens `/` at 320 × 640 and finds **Your ticket** in the banner;
2. checks the page does not scroll sideways (`scrollWidth` ≤ 320);
3. checks both action buttons have the same height, under 44 px, so neither wraps;
4. clicks the link and reaches "No open ticket on this phone" at `/t/`.

`make milestone-progress`: `14 milestone(s): up to date`. This follow-up closes nothing, so it assumes no issue
closed. `ruff check .` and `ruff format --check .` are clean.

**By hand** (demo data, the in-app browser):

- At 320 px before the fix, "Your ticket", "Sign in" and "BK ClinicQ" each wrapped onto two lines. After it,
  the bar is one line (60 px high, buttons 38 px).
- On desktop the button sits beside Sign in, after the page links.
- Clicking it opened `/t/`, which went straight to `T001`, the ticket that browser had followed.

**Not checked:** a screen reader reading the brand-only logo, beyond the name remaining in the link's text.

## Screenshots

| The home page at 320 px |
|---|
| ![The home page top bar at 320 px with Your ticket beside Sign in, on one line](https://github.com/Billykat7/clinicQ/blob/06d26dd78e5ddea89df3708337838a72cef76a5a/docs/GITHUB/PR/M9/assets/pr200/home-your-ticket.png?raw=true) |

## Risk and rollback

- **Every front-door page gains a button.** It is a plain link to `/t/`, which answers for anyone.
- **A stale `landing.css`** in a browser's cache shows the bar as before, with the new button possibly wrapping,
  until the file is fetched again. Static files carry no cache header.
- **No migration, no setting.** Rollback is a revert.

Refs #200
