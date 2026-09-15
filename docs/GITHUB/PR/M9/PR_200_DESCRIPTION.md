# PR: Let a patient sign in by phone and join a clinic's queue from its page (Issue 200 / M9-200)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#200](https://github.com/Billykat7/clinicQ/issues/200)

The clinic page's **Join the queue** button (Issue 35) linked to `/discover/clinics/{slug}/join`, which Issue
40 was meant to serve. Issue 40 shipped the API only, so with `PATIENT_JOIN_ENABLED=true` the button answered
**404**, and a patient could not sign in or join from the web. The testing guide (PR #199) had to stand in with
a console snippet.

This PR builds that page. A patient goes from the clinic page to their ticket in four steps:

1. their phone number;
2. the code sent by SMS;
3. the notifications question, in Issue 21's words;
4. one of the clinic's queues that take remote joins.

The installed app's start page (`/t/`) gets the same sign-in. That matters on an iPhone, where a Home Screen app
keeps its own storage and could not otherwise find a ticket joined in Safari. The home page gets a **Your
ticket** button beside **Sign in** that opens `/t/`, so a patient can get back to their ticket, or sign in to
find it, from the front door.

## Scope

- **In:**
  - the join page and its two scripts;
  - the sign-in on `/t/`, and **Your ticket** in the front door's top bar and footer;
  - the testing guide using the page instead of the snippet;
  - Issue 200 in the M9 milestone, its progress and the v0.9.0 note.
- **Out:**
  - signing out, and the other consent answers (the patient's consent page, Issue 21);
  - booked appointments (Issues 80 and 81);
  - a form that works with JavaScript off: the page says so, and the desk issues walk-ins;
  - switching `PATIENT_JOIN_ENABLED` on by default.

## Summary

- **The join page** at `/discover/clinics/{slug}/join`:
  - **Phone:** the number, with the reason it is asked for (the notice the code request answers with).
  - **Code:** the SMS code. "Send a new code" waits out the cooldown, counting down, and "Use a different
    number" goes back.
  - **Question:** the notifications question, preselected with the patient's current answer (no until they
    say yes).
  - **Queue:** only the queues that take remote joins, each with the clinic page's own waiting and wait
    figures, and an optional reason.
  - **Then** the ticket page.
- **A signed-in patient starts at the queue**, with a line saying whether they will be messaged. Joining a
  queue they already hold opens that ticket (the API answers `200` with it).
- **Every refusal is the API's own sentence**, in an announced alert:
  - an unreadable number;
  - a wrong, expired or locked code, with the tries left;
  - a second code inside the cooldown or over the budget (`429`, counting down from `Retry-After` and holding
    the button);
  - SMS switched off (`503`);
  - a closed, full or rate-limited join.
- **When joining is not possible, the page says why and has no form.** That covers the flag off, the clinic
  closed and no remote queue, each with the reason the clinic page's disabled button gives.
- **`/t/` signs a patient in** inside the app's scope, then reloads `/t/`, which already sends a signed-in
  patient to their open ticket. A signed-in patient with no open ticket is told so.
- **Your ticket on every front-door page** (`/`, `/features`, `/privacy`, `/terms`, `/register-clinic`), in
  the top bar and the footer, opens `/t/`.

## Design notes

- **A server-rendered page over the existing API, not new form routes.** The route decides what to show as a
  `JoinPage` dataclass: the step, the queues, the reason and the words. That is tested as data, like the rest
  of discovery. The scripts call the same endpoints every channel calls, so the page cannot disagree with
  them about why a join was refused, and no second copy of the sign-in, consent or join logic exists.
- **One sign-in, two pages.** `patient/_sign_in.html` and `patient-sign-in.js` are shared by the join page and
  `/t/`. Their styles are in `components.css`, because `/t/` does not load `discover.css`. The join page adds
  its two steps in `patient-join.js`.
- **The sign-in on `/t/` stays inside `/t/`.** A link out to `/discover` would open Safari from an iPhone Home
  Screen app, whose cookies are separate. Signing in on the start page is the only way that app can find the
  patient's ticket.
- **One sentence the API does not give.** A number shorter than 6 or longer than 32 characters is refused by
  request validation, which sends a list, not a sentence. `INVALID_PHONE_MESSAGE` is now one constant in
  `src/commons/phone.py`, raised by the API for every other unreadable number and handed to the page for
  this case, so both say the same thing.
- **The cooldown belongs to the number.** After a code is sent, only "Send a new code" waits. "Use a different
  number" frees "Send me a code", so a patient who mistyped their number can send to the right one at once.
  If they send to the same number again, the server's `429` holds the button for its `Retry-After`.
- **Your ticket sits with Sign in, not with the page links.** The top bar hides its links below 60rem, which
  is every phone, so a link there would never reach a patient. At 26rem and below the actions tighten and the
  brand keeps only its mark (the name stays for screen readers), so both labels fit on one line at 320 px.
- **The notifications answer is asked after sign-in, not before.** It is stored against the patient, so it
  needs one. A patient who is already signed in is not asked again. The queue step says what their answer is,
  with a link to change it.

## Changes

- **`src/web/join.py`** (new): the route, `JoinPage`, `JoinStep`, `JoinQueueOption` and `SignInView`. The
  route sends `Cache-Control: no-store`, and a clinic a patient may not see gets the clinic page's 404.
- **`src/templates/discover/join.html`, `src/templates/patient/_sign_in.html`** (new): the page and the shared
  sign-in.
- **`src/static/js/patient-sign-in.js`, `src/static/js/patient-join.js`** (new): the steps, over the API, with
  focus moved to each step's heading.
- **`src/templates/patient/home.html`, `src/static/js/patient-home.js`, `src/web/ticket.py`:** the sign-in on
  `/t/`, and the signed-in, no-ticket state.
- **`src/static/css/components.css`, `src/static/css/discover.css`:** the sign-in and join styles, tokens only.
- **`src/templates/web/partials/lp_header.html`, `lp_footer.html`, `src/static/css/landing.css`:** **Your
  ticket** in the top bar and the footer, and the bar kept to one line on a small phone.
- **`src/commons/phone.py`:** `INVALID_PHONE_MESSAGE`, no change in words.
- **`src/core/config.py`, `.env.example`:** `PATIENT_JOIN_ENABLED`'s description names the join page. The
  default is unchanged.
- **`src/main.py`, `src/web/discover.py`:** the router registered; comments point at the page.
- **Tests:** `tests/integration/discovery/test_join_page.py` (13, asserting the page's data, never its HTML)
  and `tests/e2e/patient/test_join_page.py` (5).
- **Docs:**
  - `docs/OPS/PATIENT_APP_TESTING.md`: sections 0, 1, 2, 3, 7 and 8 use the page;
  - the Issue 200 spec, the M9 milestone and its diagram, `docs/GITHUB/ISSUES/README.md`,
    `docs/GITHUB/README.md`, `docs/GITHUB/MILESTONES/README.md`, `README.md` (111 issues, progress with 200
    closed);
  - `RELEASE_v0_9_0.md` gains the join page.

## Testing

**The new tests, and the clinic page's, on PostgreSQL** (`TZ=UTC`):

```text
tests/integration/discovery/test_join_page.py::test_a_visitor_starts_at_the_phone_number_and_is_asked_the_consent_wording PASSED
tests/integration/discovery/test_join_page.py::test_a_signed_in_patient_starts_at_the_queue PASSED
tests/integration/discovery/test_join_page.py::test_only_queues_that_take_remote_joins_are_offered_in_the_clinics_order PASSED
tests/integration/discovery/test_join_page.py::test_when_joining_is_not_possible_the_page_says_why_and_offers_no_queue[switched-off] PASSED
tests/integration/discovery/test_join_page.py::test_when_joining_is_not_possible_the_page_says_why_and_offers_no_queue[closed] PASSED
tests/integration/discovery/test_join_page.py::test_when_joining_is_not_possible_the_page_says_why_and_offers_no_queue[walk-in-only] PASSED
tests/integration/discovery/test_join_page.py::test_the_page_is_served_never_cached_and_starts_where_the_visitor_is PASSED
tests/integration/discovery/test_join_page.py::test_a_clinic_a_patient_may_not_see_is_the_same_404_on_the_join_page PASSED
tests/integration/discovery/test_join_page.py::test_switched_off_the_page_is_served_with_the_reason_and_no_form PASSED
tests/integration/discovery/test_join_page.py::test_the_page_signs_in_records_the_answer_joins_and_opens_the_same_ticket_twice PASSED
tests/integration/discovery/test_join_page.py::test_each_refusal_the_page_shows_is_a_sentence_from_the_api PASSED
tests/integration/discovery/test_join_page.py::test_with_sms_switched_off_the_code_request_refuses_in_words PASSED
tests/integration/discovery/test_join_page.py::test_the_app_start_page_offers_the_sign_in_to_nobody_and_not_to_a_signed_in_patient PASSED
tests/e2e/patient/test_join_page.py::test_a_new_patient_joins_from_the_clinic_page_by_keyboard_on_a_320_px_screen PASSED
tests/e2e/patient/test_join_page.py::test_each_refusal_shows_the_apis_own_sentence PASSED
tests/e2e/patient/test_join_page.py::test_a_signed_in_patient_starts_at_the_queue_and_opens_the_ticket_they_hold PASSED
tests/e2e/patient/test_join_page.py::test_the_app_start_page_signs_a_patient_in_and_opens_their_ticket PASSED
======================== 30 passed in 144.49s (0:02:24) ========================
```

The other 13 of the 30 are `tests/integration/discovery/test_clinic_detail.py`, all passing.

**After adding Your ticket**, the Issue 200 tests again with the public front-door tests
(`tests/integration/public`), and the unit tests:

```text
21 passed, 9 warnings in 35.94s
1212 passed, 3 xfailed, 11 warnings in 42.74s
```

`test_the_home_pages_your_ticket_fits_a_320_px_phone_and_opens_the_app_start_page` opens `/` at 320 px. It
finds **Your ticket** in the banner, checks the page does not scroll sideways and both action buttons are
one line high (under 44 px), then clicks through to "No open ticket on this phone".

**The browser tests drive the page's own scripts** in Chromium against a running server:

- The first test clicks **Join the queue** on the clinic page. It submits the phone and the code with the
  Enter key, checks the focus lands on the code field and then on the question's heading, answers yes,
  types a reason and joins. It then checks the page is no wider than 320 px, the URL is the new ticket's
  page, and the database holds the consent (`True`) and the reason.
- The refusal test reads the page's alert for `123`, a wrong code ("That code is not right. 4 tries left.")
  and the same number again ("Too many code requests. Try again shortly.", send button disabled, countdown
  shown), and checks only one SMS was sent.

**The whole suite, as CI runs it** (`pytest tests/ -n auto --dist loadscope`, PostgreSQL and Redis):

```text
1 failed, 2418 passed, 1 skipped, 9 xfailed, 1421 warnings in 502.33s (0:08:22)
FAILED tests/e2e/display/test_board_resilience.py::test_a_day_offline_leaves_the_heap_the_listeners_and_the_layout_where_they_were
```

That test is the M8 board's day-offline soak: listeners 45 against 39 under the full parallel load. It does
not load any file this PR changes. Run alone, it passes:

```text
1 passed, 7 warnings in 89.88s (0:01:29)
```

**Quality:** `ruff check .` and `ruff format --check .` clean; `mypy src/`: `Success: no issues found in 303
source files`; unit tests `1212 passed, 3 xfailed`.
`make milestone-progress-check ARGS='--assume-closed 200'`: `14 milestone(s): up to date`.

**By hand, on the demo data** (`make seed-dev-data`, `PATIENT_JOIN_ENABLED=true`, `SMS_ENABLED=true`, a
375 px viewport):

- Hillbrow's **Join the queue**, then `082 555 0606`, read the code from `/dev/outbox`, a wrong code first,
  then the right one, yes to messages, Triage with a reason. Result: ticket `T001`, "You are next".
- Already signed in, joining General consultation, where that patient held `A004`, opened `A004`.
- **Your ticket** on the home page, at 320 px and on desktop, opened `/t/`, which went straight to the ticket
  that browser had followed (`T001`).
- Signed out with the phone's kept tickets cleared, `/t/` showed the sign-in. Signing in with `082 555 0606`
  opened `T001` at `/t/2rmKq0WM…`, never leaving `/t/`.

**Not checked:**

- **An iPhone Home Screen app**: the `/t/` sign-in is checked in Chromium only.
- **A screen reader:** the alert role and focus moves are asserted, not listened to.
- **The full Tab order** of every step: the flow was driven with Enter and clicks.

## Screenshots

At 320 px, from `tests/e2e/patient/test_join_page.py` with `TICKET_PAGE_SHOTS` set.

| Phone number | Code | Notifications question |
|---|---|---|
| ![The join page asking for a mobile number, with why it is asked](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/1-phone.png?raw=true) | ![The code step, with the resend button waiting and a 60 second countdown](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/2-code.png?raw=true) | ![The notifications question in the consent wording, with yes and no](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/3-consent.png?raw=true) |

| Queue | The ticket it opens | A refused second code |
|---|---|---|
| ![Choose a queue with Triage selected, a reason typed, and the messages line](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/4-queue.png?raw=true) | ![The ticket page for T001, you are next](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/5-ticket.png?raw=true) | ![Too many code requests, with the send button held and a countdown](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/refused-cooldown.png?raw=true) |

| The home page's Your ticket | The installed app's start page |
|---|---|
| ![The home page top bar at 320 px with Your ticket beside Sign in, on one line](https://github.com/Billykat7/clinicQ/blob/953954dc405960a8095453063332e363169a1561/docs/GITHUB/PR/M9/assets/pr200/home-your-ticket.png?raw=true) | ![No open ticket on this phone, with the phone sign-in below](https://github.com/Billykat7/clinicQ/blob/76e6860c8c9ca9c15782fd8555c4015ae302b294/docs/GITHUB/PR/M9/assets/pr200/app-start.png?raw=true) |

## Acceptance criteria

- [x] With the flag on and the clinic open, a new patient goes from the clinic page to their ticket page by phone number, code, the notifications answer and a queue, in one browser, with no console (`test_a_new_patient_joins_from_the_clinic_page_by_keyboard_on_a_320_px_screen`; by hand, `T001`)
- [x] The notifications answer given on the page is the one recorded, and the question is the wording in `consent_text.py` (the browser test reads the legend and the stored consent; the integration test records "no")
- [x] A signed-in patient opens the page at the queue step; joining a queue they already hold opens that ticket, not a second one (`test_a_signed_in_patient_starts_at_the_queue_and_opens_the_ticket_they_hold`, one ticket in the database)
- [x] Only queues that take remote joins are offered (`test_only_queues_that_take_remote_joins_are_offered_in_the_clinics_order`)
- [x] A second code within the cooldown, SMS switched off, an unreadable number and a wrong code each show the API's own sentence, and the resend waits out the cooldown (the browser refusal test, `test_each_refusal_the_page_shows_is_a_sentence_from_the_api`, `test_with_sms_switched_off_the_code_request_refuses_in_words`)
- [x] With the flag off, the clinic closed, or no remote queue, the page says why and offers no form (the three `test_when_joining_is_not_possible…` cases, `test_switched_off_the_page_is_served_with_the_reason_and_no_form`)
- [x] The `/t/` start page signs a patient in and opens their open ticket, without leaving `/t/` (`test_the_app_start_page_signs_a_patient_in_and_opens_their_ticket`; by hand). Not checked on an iPhone
- [ ] The page fits a 320 px screen, and every step is reachable by keyboard with its errors announced. **Partly:** 320 px is asserted and shown; the flow runs by keyboard (Enter), focus moves to each step's heading, and errors are `role="alert"`. The full Tab order and a screen reader were not checked

## Risk and rollback

- **Off by default.** With `PATIENT_JOIN_ENABLED` unset, the clinic page's button stays disabled and the join
  page says joining from a phone is not switched on. Nothing changes for a deployment until it sets the flag.
- **Every front-door page gains a button.** It is a plain link to `/t/`, which answers for anyone. On a phone
  the brand name is hidden visually to make room; it is still the link's accessible name.
- **`/t/` now offers sign-in to anyone with no session.** It calls the same public code request as before,
  under its existing per-number and per-address limits, and signing in only reveals the patient's own ticket.
- **A stale stylesheet shows the new forms unstyled.** Static files carry no cache header, so a browser that
  kept the old `components.css` or `discover.css` draws the sign-in as plain fields until it fetches them
  again. It happened here in development and was fixed by fetching the files again. This is true of any CSS
  change today, but these forms are new, so it is more visible.
- **No migration and no new API.** Rollback is a revert. The button then goes back to the 404 while the flag
  is on.

Closes #200
