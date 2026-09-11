# PR: Base UI shell: three layouts on one token set, htmx, and nothing from a CDN (Issue 5 / M1-05)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#5](https://github.com/Billykat7/clinicQ/issues/5)

Every ClinicQ screen now starts from one base document and one of three layouts: **patient**
(mobile-first, on the patient's own phone), **dashboard** (dense, desktop, for staff) and **board**
(the waiting-room TV, read from five metres). All three are built from one set of design tokens and
one component library. Decision 2 came first: the team kept the hand-written tokens over Tailwind,
and dropped Alpine.js, which this CSP cannot run. The rest of the branch follows from that decision.
The kernel console moved into the dashboard layout without a single computed style changing, the
fonts are now self-hosted so no page asks another host for CSS, and a development-only catalogue
documents each component beside the call that renders it.

## Screenshots

Each layout at its real size (phone at 390×844, desktop at 1440×900, TV at 1920×1080), rendered by
headless Chrome with the colour scheme emulated. Same tokens, same components; only the layout and
the theme differ.

| | Light | Dark |
|---|---|---|
| **Patient** | ![Patient layout, light](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/patient-light.png?raw=true) | ![Patient layout, dark](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/patient-dark.png?raw=true) |
| **Dashboard** | ![Dashboard layout, light](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/dashboard-light.png?raw=true) | ![Dashboard layout, dark](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/dashboard-dark.png?raw=true) |
| **Board** | ![Board layout, light](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/board-light.png?raw=true) | ![Board layout, dark](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/board-dark.png?raw=true) |

The component catalogue at `/dev/components`:
[components-light.png](https://github.com/Billykat7/clinicQ/blob/6616941cda7a6e8a1d79e367d174d119105cc87a/docs/GITHUB/PR/M1/assets/pr5/components-light.png?raw=true).
The files are in `docs/GITHUB/PR/M1/assets/pr5/`.

## Summary

- **Decision 2, settled and written down:** the tokens stay, with no Tailwind and no Alpine (see
  *Design notes*). It is recorded in `docs/GITHUB/ISSUES/README.md`, and the wording that said
  Tailwind or Alpine is corrected in the Issue 5 spec, the M1 milestone, the tech docs, the
  implementation plan, the workload split and the README.
- **One token set:** `site.css` gains a type scale (`--step--2` to `--step-5`), a spacing ladder
  (`--space-1` to `--space-8`), the layer scale, a card shadow and the toast washes. The layouts
  choose which step to read; none of them defines a colour.
- **One base, three layouts:** `base.html` is now the root document (blocks: `title`, `styles`,
  `layout_head`, `head`, `body_class`, `body`, `content`, `scripts`). The kernel's signed-in shell
  moved, unchanged, into `layouts/dashboard.html`, and the 21 kernel pages extend it.
  `layouts/patient.html` and `layouts/board.html` are new.
- **Component library:** Jinja macros in `src/templates/components/` (button, card, badge and
  ticket-status badge, empty state, toast, skeleton, theme toggle) over `components.css`. The kernel
  already had these styles in `admin.css`, which only the console loads; they moved out, so the
  patient page and the board use the same button rather than a copy of it.
- **No runtime CDN request:** DM Sans and Roboto are self-hosted (160 KB of woff2, OFL licences
  alongside), so both Google hosts left the CSP. htmx was already vendored; now its config fits the
  policy too.
- **htmx wiring:** a fragment swap that raises a toast from the server through `HX-Trigger`, and a
  lazy load behind a skeleton.
- **`/dev/components` and `/dev/layouts/{patient,dashboard,board}`**, registered in development
  only.

## Design notes

**Why tokens and not Tailwind (decision 2).** The kernel already styles every screen with tokens,
in light and dark, and guard tests hold its CSP. Tailwind would add a build step to every laptop,
the Docker image and CI. It would also leave the console on the tokens until someone rewrote it,
so the project would carry two design systems, the very thing this issue exists to prevent. What
the spec wanted from Tailwind (one stylesheet, tokens for colour, type and spacing) the tokens now
provide.

**Why no Alpine.** Alpine's standard build evaluates expressions with `new Function`, which a
policy without `'unsafe-eval'` blocks. Its CSP build only accepts property-access expressions,
which removes the point of using it. The local UI state these screens need (a toast, a live clock)
is a few lines in `src/static/js/`, under the same policy as everything else. That is also how the
kernel already works. `base.html` sets htmx's config so it fits the policy:
`includeIndicatorStyles: false`, because its injected style element would be blocked (the indicator
rules live in `components.css`), and `allowEval: false`, so `hx-on` fails closed instead of
throwing CSP errors.

**Fonts: self-hosted, agreed before any code.** Every page loaded DM Sans and Roboto from Google Fonts, which
is a runtime CDN request for CSS, exactly what the criteria forbid. It also sent every visitor's IP
address to Google, and it would leave a clinic's board in a fallback typeface whenever the internet
drops. Issue #181 had kept the Google hosts and called self-hosting "a separate change with its own
trade-off"; this is that change, and its test now asserts the opposite. The trade-off is 160 KB of
fonts served and cached by this app, subset to latin and latin-ext, which cover English, Afrikaans
and the Latin-script South African languages. `base.html` preloads the one file every page needs
first.

**The move changed nothing the console shows.** Moving rules from `admin.css` into a file loaded
before it can flip the cascade. Every other rule on those classes was audited: all have higher
specificity (`.btn[hidden]`, `.ac-row > .btn`, `.device-cell .badge`), so load order cannot matter.
Then it was measured. A page carrying every moved component (39 elements) was styled once with
`main`'s CSS and once with this branch's, both with the same fonts, and every computed property
was compared in both themes. **37,206 properties, 0 differences.** The colour literals in the moved
rules became tokens with identical values (`16px` is `--radius-lg`, `999px` is `--radius-pill`).

**The layouts differ in frame and scale, not colour.** Patient: one 30rem column, a sticky header
with the brand and theme toggle, 44px targets, the ticket number at `--step-4`. Dashboard: the
kernel console as it was, dense and wide. Board: a full-screen grid with no header, navigation,
scrolling or cursor. The number being served is at `--step-5` (176px on a 1080p TV), and a portrait
screen stacks the two panels. A guard fails on any colour literal in `components.css`,
`layouts.css` or `dev.css`, so a layout cannot drift into its own palette.

**Contrast is computed, not eyeballed.** A unit test parses the palettes from `site.css` and checks
the WCAG 2.2 AA ratio (4.5:1) for all 13 text/background pairs the components use, in both themes.
The lowest is the warning badge at 5.10:1. The same test found that the dark palette is written
twice, for the OS preference and for the toggle, and now fails if the two copies ever differ.

**Behaviour, not markup (the testing rule).** Nothing asserts rendered HTML. The routes are checked
by status code, redirect and header; the rules by reading source, the way `test_no_inline_styles.py`
does; what a browser does (the swap, the toast, the themes) is shown here with outputs and
screenshots.

**Ticket status has one look.** `TICKET_STATUS_BADGES` in `src/web/components.py` gives each
`TicketStatus` its words ("No-show", not `no_show`) and tone, and templates call
`status_badge(status)`, so no template compares a status with a string. A status added to the enum
without a badge fails a test. `TICKET_SOURCE_LABELS` does the same for channels.

**Development pages exist only in development.** `create_app()` registers `/dev/*` only when
`ENVIRONMENT=development`, so staging and production answer 404 rather than a 403 that admits the
page exists. There is no `debug` setting in this app; the environment is what already gates `/docs`.
Tabs are URLs, per the list-view rule: `/dev/layouts` and an unknown layout redirect to the patient
sample, and a fragment opened directly redirects to its page.

**Out of scope:** the dashboard screens (M7), the board (M8) and patient pages (Issues 32, 68). The
dashboard sample shows the console signed out, without the icon rail. The rail needs a signed-in
session, and signing in means typing a password, which this verification did not do.

## Changes

- **`src/templates/base.html`:** the root document.
- **`src/templates/layouts/`** (new): `dashboard.html` (the kernel shell, moved), `patient.html`,
  `board.html`.
- **21 kernel templates:** `{% extends "layouts/dashboard.html" %}`.
- **`src/templates/components/`** (new): `button`, `card`, `badge`, `empty_state`, `toast`,
  `skeleton`, `theme_toggle`.
- **`src/static/css/site.css`:** `@font-face` rules and the new tokens.
- **`components.css`** (new; rules moved from `admin.css` plus `.btn-block`, the htmx indicator and
  empty-state text), **`layouts.css`** (new), **`dev.css`** (new); **`admin.css`** loses the moved
  rules (131 lines).
- **`src/static/fonts/`** (new): four woff2 files and two OFL licences.
- **`src/static/js/ui-feedback.js`:** server toasts from a `<template>`, from `HX-Trigger`, and from
  `data-toast-message` buttons. **`board-clock.js`** (new) keeps the board's clock in Johannesburg
  time.
- **`src/web/components.py`** (new): `BadgeTone`, `ButtonVariant`, `ToastKind`, the status and
  source vocabularies, `toast_trigger()`, registered as Jinja globals.
- **`src/web/dev.py`** (new), **`src/templates/dev/`** (new) and **`src/main.py`:** the development
  pages, registered in development only.
- **`src/core/security_headers.py`:** `style-src 'self' 'nonce-…'` and `font-src 'self'`.
- **`src/templates/web/{index,features,privacy,terms}.html`:** the Google Fonts links replaced by a
  local preload.
- **Tests (new):** `tests/unit/platform/test_ui_shell.py` (37) and
  `tests/integration/platform/test_ui_shell_routes.py` (28);
  `test_security_headers.py`'s font-host test now asserts this origin alone.
- **Docs:** decision 2 in `docs/GITHUB/ISSUES/README.md` (with the *Where code goes* rows); the
  Issue 5 spec, the M1 milestone, `13-tech-implementation.md`, `REPO_README.md`,
  `IMPLEMENTATION_PLAN.md`, `WORKLOAD_SPLIT.md` and the README stop promising Tailwind and Alpine.
  Issue titles are unchanged, because the sync script finds issues by them.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (159 files)
- [x] `make test`: **840 passed** (775 on `main` plus 65 new). 24 fail, exactly the 24 that fail on
      `main`, compared with `diff` (the workflow, `.gitleaks.toml` and `docs/SECURITY/` guards)
- [x] The htmx swap, live in the browser on `/dev/layouts/dashboard`: clicking Refresh re-rendered
      the rows on the server and raised the toast through `HX-Trigger`, and the stat tiles had
      replaced their skeleton on load:

      ```text
      rows rendered at   2026-09-11T07:45:58+02:00  ->  2026-09-11T07:47:55+02:00   swapped: true
      toast              "Queue refreshed at 07:47:55"
      stat tiles         4 lazy-loaded, 0 skeletons left
      htmx.config        allowEval: false, includeIndicatorStyles: false
      ```

- [x] No CDN request: every request the patient and dashboard pages made went to
      `localhost:8015` (the CSS, `/static/fonts/*.woff2`, htmx, the page scripts, the fragments).
      No console errors on the patient page. On the dashboard, the only errors are the kernel
      shell's own signed-out probes (`/api/v1/auth/me` and `/auth/refresh` answering 401), which
      any console page shows when nobody is signed in
- [x] No unstyled flash: the stylesheets are render-blocking in `<head>` and `theme.js` sets
      `data-theme` before first paint. The screenshots above were taken at load, not after a
      settle-and-restyle
- [x] The guards fail when they should. A `#0f6e56` added to `layouts.css` and a
      `<script src="https://unpkg.com/alpinejs">` added to `layouts/board.html`:

      ```text
      E  AssertionError: Runtime CDN reference (vendor it under src/static/):
      E    src/templates/layouts/board.html:17
      E  AssertionError: Colour literal (use a var(--token) from site.css):
      E    layouts.css:140: #0f6e56
      2 failed, 35 passed
      ```

- [x] Computed-style equality of the moved components, `main` against this branch: 37,206
      properties across 39 elements in light and dark, 0 differences
- [x] Template punctuation check (`.cursor/rules/template-punctuation.mdc`): the only hits are the
      existing em-dash placeholders and the verbatim USSD transcript
- [ ] Linux and a real kiosk TV: checked in Chrome on macOS only

## Acceptance criteria

- [x] A page extending `base.html` renders with the shared token stylesheet (compiled Tailwind
      before decision 2) and no unstyled flash
- [x] An htmx fragment swap works end to end on a demo route (`/dev/layouts/dashboard` and
      `/dev/components`; output above)
- [x] The three layouts are visually distinct but share tokens, proven by the screenshots above (and
      by the guard: no colour outside `site.css`)
- [x] No runtime CDN request is made for CSS or JS (nor for fonts: the network log, the CSP and two
      guards)
- [x] Components are documented on a `/dev/components` page available only in debug mode (in
      development only; 404 in staging and production)
- [x] Base type and contrast meet WCAG 2.2 AA in the default theme (13 pairs at 4.5:1 or better,
      in the dark theme too; 16px body text)

## Risk and rollback

The visible risk is the console: 21 pages changed their parent template, and 131 lines of CSS
moved. The computed-style comparison says the result is identical, and the kernel's own suites
pass. The fonts are the same families, now served by this app, so a deployment serves 160 KB more
static files and makes no request to Google. The CSP is tighter; nothing else depended on the
Google hosts. No migration. Rollback is a revert of this PR.

**Follow-ups found along the way:**

- `admin.css` (62 colour literals, including the rail's per-module colours) and `landing.css` (41)
  predate the token rule. The guard lists them as not yet covered; moving them onto tokens is its
  own change.
- Borders use `--line`, about 1.4:1 against a card. That is fine for decoration and for buttons,
  which carry their own label, but a form field identified only by its border needs 3:1 (WCAG
  1.4.11). The first form component should add a stronger border token.
- `scripts/lint_surface_gates.py` reads only `src/web/routes.py`, so a page route in any other
  module under `src/web/` goes unchecked. `src/web/dev.py` is ungated on purpose (development only),
  but the next module will not be.

Closes #5
