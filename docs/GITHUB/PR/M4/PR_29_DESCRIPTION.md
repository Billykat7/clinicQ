# PR: A clinic can sign itself up, and a platform admin checks it first (Issue 29 / M4-29)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#29](https://github.com/Billykat7/clinicQ/issues/29) · **Builds on:** #23 (PR #138), #24 (PR #139), and #25/#27/#26/#28 beneath it

> **Merge order:** after #23–#28. This branch is stacked on them, so the Conventions check fails on
> their commits until they merge. Every test job passes.

A discovery directory is only trustworthy if the entries are real. Anyone can submit a clinic; a
platform admin checks it before it becomes visible, which also gives the pilot a controlled way to
add sites without a developer running SQL.

## Summary

- **The public registration form** (`/register-clinic`) on the front door, following
  `web/privacy.html`: `landing.css`, the shared `lp_header` / `lp_footer` partials, no inline styles
  or handlers, and `public_page_context`, which takes no database session and so still renders
  during an outage.
- **`POST /api/v1/sites/register`** takes no session **and grants nothing**: a clinic in
  `pending_verification`, invisible to every patient-facing surface, with no account, no role and no
  session attached. It arrives **ready** — default queues and a services catalogue — so an approving
  admin sees a working clinic rather than an empty shell.
- **The verification console** (`/admin/verification/{pending|verified|draft|suspended}`) follows
  `docs/IDE/RULES/list-view-ui-pattern.mdc`: a tab per URL, a collapsible filter bar with its own
  remembered state, click-to-sort columns, and a row click that opens the kernel's anchored
  slideover beside the list.
- **The lifecycle is a state machine with one writer.** `ALLOWED_TRANSITIONS` is the whole of it and
  `transition()` is the only thing that writes `site.status` — the same shape non-negotiable 2 gives
  a ticket's status, and for the same reason.
- **An unverified clinic never appears in a discovery search**, proven against
  `src/modules/sites/discovery.py:search_sites` — **the search service, not the page** — which is
  the narrowing discovery (Issue 31), the detail page (Issue 35) and both channel menus all go
  through.
- **A suspended clinic stops accepting joins immediately**, because the join gate reads the status
  on every request. No cache to invalidate, no list to rebuild.
- **Every status change is audited** with the deciding admin, and **publishes an event** rather than
  sending anything: delivery is the notification service's (Issue 63).

## Design notes

**There is no `site_registration` table, and that is a decision.** A submission *is* the clinic — it
has a name, a sector, an address and a coordinate from the moment it is put forward. A separate row
would mean copying all of it across on approval and then keeping two records of the same place, with
the usual consequence that one of them becomes stale. What a submission adds is a **contact** and a
**decision**, so migration `0013` adds seven nullable columns to `site` and the lifecycle stays
`site.status`, which Issue 23 already created.

**The public endpoint is safe to leave open because a submission is worthless.** It creates no
account, grants no role, issues no session, and produces a row no patient-facing surface returns.
The worst a stranger can do is put an entry in a queue a platform admin reads. That reasoning is
written into both allow-lists it needed (`test_api_route_gates.PUBLIC` and
`lint_surface_gates._UNGATED_PAGES`) rather than left implied.

**A slug clash is refused without saying whose it is.** `"The web address 'hillbrow-chc' is already
in use. Please choose another."` — a stranger filling in a form must not be able to learn from a
refusal whether a clinic exists and is listed. There is a test asserting the message says neither
"verified" nor "pending".

**A rejection with no words is the one thing this workflow must not produce.** Sending a clinic back
or suspending it **requires** a note, because the submitter is shown exactly what the admin writes;
the refusal message says so. Approving needs none.

**`verified → pending_verification` is a legal move.** "We need more information" about a clinic
that is already listed has to be possible without suspending a working clinic, so a platform admin
who is unsure has a step short of switching it off.

**A clinic waiting to be checked can walk through its own queue; a draft one cannot.** The issue
says only verified clinics appear in discovery and "the rest are reachable by direct link for
testing", so `NOT_TAKING_PATIENTS` refuses `suspended` and `draft` and deliberately does not refuse
`pending_verification`: a clinic about to go live has to be able to exercise its queue, and only
somebody who already has its link can reach it. A draft has not been put forward at all. The line is
named in a constant with that paragraph beside it, and there is a test for both sides of it.

**Three bugs the tests were green through, and the browser was not.** Driving the pages against a
real database found all three:

- **the CSRF token.** Three of this milestone's scripts matched a **hardcoded** cookie name
  (`csrf_token`) instead of the configured one (`bk_clinicq_csrf`, read from
  `<meta name="bkp-csrf-cookie">`). Every submission from a browser that had a CSRF cookie was
  refused with "Invalid or missing CSRF token". All three now use `window.BKP.writeHeaders()`, the
  helper that exists for exactly this, and the register form's header says why;
- **the console's slideover.** It reached for `window.A` (the namespace *inside* `admin-crud.js`)
  rather than `window.BKPAdmin`, and the page did not load `admin-crud.js` at all, so the panel had
  no geometry and opened off the anchor. Both fixed; the panel is now the kernel's, with its
  standard head/foot chrome and outside-click dismissal.
- **the form greeted every visitor with two red fields.** `.lp-field input:invalid:not(:placeholder-shown)`
  was written to mean "red once they have typed something wrong", but `:not(:placeholder-shown)`
  matches any input that has **no `placeholder` attribute at all** — which is exactly *Full name* and
  *Work email address*. Both were painted with the danger border the instant the page loaded, before
  anyone had touched them. Now `:user-invalid`, which is the selector that actually means that.

**Out of scope:** payment and medical-aid details (Issue 37); the notification delivery itself
(Issue 63), which subscribes to the event this raises; and the discovery API (Issue 31), which is
built on the `search_sites` narrowing added here.

## Changes

- **`alembic/versions/0013_site_onboarding.py`** (new): seven columns on `site` plus the console's
  `(status, submitted_at)` index. **`src/database/models/site.py`:** the same columns.
- **`src/modules/sites/onboarding.py`** (new): `ALLOWED_TRANSITIONS`, `submit_registration`,
  `pending_queue`, `transition`. **`discovery.py`** (new): `publicly_visible` and `search_sites`.
- **`src/modules/sites/router.py`:** three routes (`POST /register`, `GET /verification`,
  `PUT /{site_id}/verification`). **`schemas.py`:** five models.
  **`availability.py`:** `NOT_TAKING_PATIENTS`, with the pending/draft line written out.
- **`src/core/domain_events.py`:** `SiteStatusChanged`.
- **`src/templates/web/register_clinic.html`**, **`src/static/js/register-clinic.js`**,
  **`src/templates/admin/verification.html`**, **`src/static/js/admin-verification.js`** (all new),
  and their routes in **`src/web/routes.py`** (`_VERIFICATION_SECTIONS`, with the bare console URL
  redirecting to its default tab and an unrecognised section redirecting rather than 404ing).
  **`src/static/css/landing.css`:** front-door form styles — this is the first form out there —
  plus a top margin on `.lp-doc h2`, given back by `.lp-doc section > h2:first-child`. A `.lp-doc`
  section with several headings needs the gap; `privacy.html` never showed it because each of its
  headings is alone in its own `<section>` and takes the gap from the section instead.
  **`src/static/css/admin.css`:** `.detail-list`, the slideover's label/value grid, which had no
  style at all and collapsed to one column under 30rem.
- **`src/static/js/site-display-settings.js`**, **`admin-verification.js`**: the CSRF fix above.
- **`tests/`:** `integration/sites/test_onboarding_api.py` (17 cases); the two allow-list entries,
  each with its reason.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (208 files); the template
      punctuation check clean; `scripts/lint_surface_gates.py` clean.
- [x] `make test`: **1411 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**, including migration `0013` down and up and `alembic
      check` finding no drift.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, migrated and seeded, then driven through the real app. Transcript, verbatim:

```text
  the public form:
    POST /sites/register (no session)  -> 201  status: pending_verification
      the submitter is told: "Thank you. A ClinicQ administrator will check these details and get
      in touch. Your clinic will not appear to patients until it has been checked."
      event raised: SiteStatusChanged to pending_verification | nomsa@zolaclinic.example
    POST a slug somebody holds         -> 409  The web address 'hillbrow-chc' is already in use.
                                               Please choose another.        ← and nothing more
    POST location 0, 0                 -> 422
    GET  /sites/verification (no session) -> 401

  invisible until verified, asserted on the SEARCH SERVICE:
    search_sites(query='zola')         -> []
    the row exists                     -> zola-north-clinic
    it arrived ready                   -> 3 queues, 6 services, display_mode: number_only
    joins by direct link               -> True          ← pending, so it can test itself

  the platform admin decides:
    GET  /sites/verification           -> 1 waiting | Zola North Clinic | nomsa@zolaclinic.example
      (as a clinic manager)            -> 403
    PUT  send back with no reason      -> 422  Say what is wrong, or what more is needed. The
                                               person who submitted this clinic is shown exactly…
    PUT  verify                        -> 200  status: verified
      event: pending_verification -> verified | by platform-admin@clinicq.example
      search_sites(query='zola')       -> ['zola-north-clinic']
    PUT  verify again                  -> 409  A clinic that is 'verified' cannot become 'verified'.
    PUT  suspend                       -> 200  status: suspended
      joins, immediately               -> False  This clinic is not accepting patients through
                                                 ClinicQ at the moment.
      search_sites(query='zola')       -> []

  audit trail:
    nomsa@zolaclinic.example       | submitted zola-north-clinic for verification
    platform-admin@clinicq.example | listing pending_verification -> verified
    platform-admin@clinicq.example | listing verified -> suspended: Reported closed; confirming
                                     with the district.
```

- [x] **Both pages were driven in a real browser** against a migrated, seeded PostgreSQL database:
      `/register-clinic` filled in and submitted as an anonymous visitor (the thank-you panel
      appears with the server's own sentence and the clinic's web address), then
      `/admin/verification/pending` opened as the platform admin, the row clicked to open the
      slideover with the contact details and the three decision buttons, a note typed, **Verify**
      clicked — after which `/verification` reports 0 waiting and `/verification?status=verified`
      carries the clinic with the note and a `reviewed_at`. That run is what found the two bugs
      above; a clinic manager opening the console gets the *Access denied* page.
- [x] **Screenshots**, captured from that same run at 2x, light and dark.

      The public form, `/register-clinic` — one column, section by section, with the sentence that
      says nothing is visible until an administrator has checked it:

      | Light | Dark |
      |---|---|
      | ![The public clinic registration form, light](https://github.com/Billykat7/clinicQ/blob/b372e2459936403b3891c6bcd786b1b121df1730/docs/GITHUB/PR/M4/assets/pr29/register-clinic-light.png?raw=true) | ![The public clinic registration form, dark](https://github.com/Billykat7/clinicQ/blob/b372e2459936403b3891c6bcd786b1b121df1730/docs/GITHUB/PR/M4/assets/pr29/register-clinic-dark.png?raw=true) |

      The console, `/admin/verification/pending` — the waiting tab with the slideover open on the
      submitted clinic, its contact details, the note field and **Verify / Send back / Suspend**:

      | Light | Dark |
      |---|---|
      | ![The clinic verification console with a clinic open, light](https://github.com/Billykat7/clinicQ/blob/b372e2459936403b3891c6bcd786b1b121df1730/docs/GITHUB/PR/M4/assets/pr29/verification-light.png?raw=true) | ![The clinic verification console with a clinic open, dark](https://github.com/Billykat7/clinicQ/blob/b372e2459936403b3891c6bcd786b1b121df1730/docs/GITHUB/PR/M4/assets/pr29/verification-dark.png?raw=true) |

## Acceptance criteria

- [x] **An unverified clinic never appears in a discovery search, proven by a test.** Asserted
      against `search_sites`, the search service, for `draft`, `pending_verification` and
      `suspended` in turn — and the same test flips the clinic to `verified` and finds it, so it is
      discriminating rather than always-red.
- [x] **A platform admin can approve, reject with a reason, or request more information.** Verify,
      "send back" (`draft`) and suspend, each through one endpoint; a rejection with no words is a
      422 saying so.
- [x] **Every status change is audited with the deciding admin.** Three rows in the transcript,
      including the submission itself, which is attributed to the contact address that made it.
- [x] **The submitter is notified at each transition.** *Partly (Issue 29).* Each transition
      **publishes `SiteStatusChanged`** carrying the decision, the note and the contact details,
      asserted published after the commit. The delivery half is the notification service's (Issue
      63), which is where the subscriber will be registered; nothing is sent from here on purpose.
- [x] **A suspended site stops accepting joins immediately.** Shown in the transcript: the join gate
      answers `False` on the next read, with a refusal that says nothing about *why* the listing is
      off.
- [x] **The verification queue is reachable only by platform admins.** `business`-tier grant on
      `sites`: a clinic manager gets 403 from the API and the *Access denied* page from the console;
      an anonymous request gets 401.

## Risk and rollback

**Migration `0013`** adds seven nullable columns and one index, and drops nothing, so the previous
release runs unchanged on the new schema and the migration is reversible — a downgrade loses the
submission trail, not the clinic. One new public route and one new public page, both of which grant
nothing; the three JavaScript CSRF fixes affect pages added in this milestone only.

**Follow-ups noticed:** the registration form has no rate limit of its own, so a determined stranger
can fill the verification queue with rubbish — it needs the same treatment the OTP endpoints have
(worth doing before the pilot, and the limiter already exists); and the contact details are
personal information with no retention rule yet, which belongs in the M13 data map (Issue 95)
alongside the `reason_text` window Issue 27 chose an interim ceiling for.

Closes #29
