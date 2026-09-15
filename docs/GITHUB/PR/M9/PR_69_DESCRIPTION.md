# PR: The ticket pages install as an app that shows the last known place in line with no signal (Issue 69 / M9-69)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#69](https://github.com/Billykat7/clinicQ/issues/69) · **Builds on:** #5 (the base shell), #68 and
#64 (PRs #187 and #188), both merged · **Unblocks:** #71

A patient most wants their place in line when they have the least signal: in a taxi, or inside a concrete
waiting room. With this PR:

- **The ticket pages are an installable app.** There is a manifest, standalone display, PNG and maskable icons
  in the brand colours, and a start page that opens the patient's ticket.
- **With no network, the app shows the last known ticket with its age**: the number, the place in line the
  phone last saw, when that was and how long ago, counting up, and "your place may have moved since".
- **A new deploy is picked up on the next launch** with nothing for the patient to clear.
- **The cache is bounded by construction.** It holds a fixed list of shell files, and at most five tickets,
  none older than 18 hours.
- **Installation is offered only after a successful join.** The browser's own banner is always held back,
  and only the patient who joined sees "Add to home screen", on their own open ticket.
- **Lighthouse's PWA checks pass** (11.7.1, score 1).

**Decisions made here:**

- **One patient worker, not a second `sw.js`.** The spec names `src/static/sw.js`, but #64 already registers
  `/patient-sw.js` for `/t/` to show web push. Two workers cannot control the same scope, so the offline shell
  is added to that worker. It stays separate from the board's (#62): different scope, different cache names,
  and each deletes only its own caches, which the browser test checks with a board cache present.
- **The app's scope is `/t/`**, the ticket pages, not the whole site. Installing never turns the staff
  dashboard or the landing page into the app, and the manifest is linked only from `/t/` pages.
- **"After a successful join" means the ticket's own signed-in patient, on an open ticket**
  (`TicketPageOut.offer_install`). A patient who joined lands on that page; a shared link never qualifies.
- **The last known state lives in Cache Storage, not `localStorage`.** Both the ticket page and the worker's
  offline page can read it, and the same bound applies wherever it is written.

**Not done here, and not claimed:**

- **Installing on a real Android phone and launching from the home screen (How to verify, step 1) has not
  been done.** It needs a phone. Chromium's own installability check reports no errors, and Lighthouse's
  installable audit passes, but that is not the device check. `docs/OPS/PATIENT_APP.md` has a place to
  record it.
- **Nothing was tried on an iPhone.** WebKit documents Home Screen web apps as keeping storage apart from
  Safari, so an iPhone app may open without the ticket followed in Safari. `WEB_PUSH.md` now says so instead
  of "arrives with Issue 69".

## Summary

- **Manifest** (`src/static/manifest.json`):
  - `id` and `scope` `/t/`, `start_url` `/t/?source=home-screen`, `display: standalone`;
  - theme `#0f6e56` (the pages' `theme-color`) and background `#f7f6f3` (the page ground);
  - icons 192 and 512, a maskable 512 with the lens inside the safe zone, and a 180 `apple-touch-icon`.
    `scripts/render_app_icons.py` draws them from the favicon's shapes with Pillow.
  - `components/pwa_head.html` links them from the ticket page, the start page and the offline page only.
- **The worker** (`src/static/patient-sw.js`, served by `GET /patient-sw.js`):
  - the server writes in the version (`VERSION`, plus the first 12 characters of `GIT_SHA` when the image
    sets it) and the shell list (`PATIENT_SHELL`), served `no-cache`;
  - **install** stores the shell in `clinicq-patient-shell-<version>`, bypassing the HTTP cache, and takes
    over at once;
  - **activate** deletes other `clinicq-patient-shell-*` caches, and nothing else;
  - **navigations under `/t/`** go to the network first; if there is no answer within 10 seconds, the offline
    page is served in the page's place;
  - **shell files** are served from the cache and refreshed behind it; everything else (JSON, the stream,
    push, preferences) is left to the network;
  - #64's push and notification-click handlers are unchanged, except the notification icon is now the 192 PNG.
- **The pages and routes** (`src/web/ticket.py`):
  - `GET /t/` is the app's start page: a `303` to the signed-in patient's open ticket today
    (`ticket_page.active_page_for_patient`), otherwise `patient/home.html`, whose `patient-home.js` opens the
    last unfinished ticket the phone kept;
  - `GET /t/offline` is `patient/offline.html` with `ticket-offline.js`: the kept state for the link opened,
    or the latest one, its update time and a running age, "Try again", and a reload when `online` fires;
  - the ticket page gains the install box (`ticket.html`, `ticket.css`) and loads `patient-tickets.js` and
    `pwa.js`. `ticket.js` saves every state it renders.
- **Kept tickets** (`src/static/js/patient-tickets.js`): `save`, `get`, `latest` and `all` over the
  `clinicq-patient-tickets` cache, one entry per link with its received time. Every save prunes to five
  entries, drops entries older than 18 hours, and accepts only ticket-link paths.
- **The install offer** (`src/static/js/pwa.js`): it registers the worker, which asks the patient nothing.
  It always calls `preventDefault()` on `beforeinstallprompt`, and shows `#tk-install` only where the server
  rendered it. The button calls the held `prompt()`. "Not now" is remembered, nothing is offered in
  standalone mode, and iPhones get the Share-menu instruction.
- **Data contract:** `TicketPageOut.offer_install` in `contracts/queue.yaml`.

## Design notes

**Why network-first for pages, with a limit.** A ticket page served from a cache is a wrong number. The page
is always asked for first, and the offline page is used only when the network fails or is still silent
after 10 seconds. A phone on a dead router does not refuse connections; it just never answers, which is the
case the browser test uses. After 10 seconds the patient sees what the phone knows instead of a blank
screen, and the page reloads itself when the connection returns.

**Why the shell is a list the server writes in.** "The cache cannot grow without limit" is easiest to keep
when nothing is cached at runtime. The worker keeps exactly `PATIENT_SHELL`. An integration test renders the
offline page and fails if it loads a file that is not on the list, and fetches every listed file, because one
missing file would stop the worker installing, and with it web push.

**Why the version includes the commit.** A release tag alone would not change between two images built from
different commits under the same version. Writing the commit in makes every image a new worker, so the next
launch installs it.

## Changes

- **New:**
  - `src/static/manifest.json`, `src/static/icons/` (4 PNGs), `scripts/render_app_icons.py`
  - `src/templates/patient/offline.html`, `patient/home.html`, `components/pwa_head.html`
  - `src/static/js/patient-tickets.js`, `pwa.js`, `ticket-offline.js`, `patient-home.js`
  - `docs/OPS/PATIENT_APP.md`
- **Changed:**
  - `src/static/patient-sw.js`, `src/web/ticket.py`
  - `src/modules/queue/ticket_page.py` (`active_page_for_patient`, `offer_install`), `schemas.py`,
    `contracts/queue.yaml`
  - `src/templates/queue/ticket.html`, `src/static/css/ticket.css`, `src/static/js/ticket.js`
  - `docs/OPS/WEB_PUSH.md` (the iPhone paragraph)
- **Tests, new:**
  - `tests/integration/queue/test_patient_app.py` (5)
  - `tests/e2e/patient/test_patient_app.py` (4)
- **Tests, updated:** `tests/unit/security/test_site_scoped_queries.py` (`active_page_for_patient` is
  narrowed to the patient's own tickets, with its reason).
- **Docs:** the Issue 69 spec (files), the M9 status row and progress bars (`--assume-closed 69`), and the
  README Status block (69 of 109).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (298 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2227 passed,
  1 skipped, 9 xfailed, 0 failed**.
- [x] **How to verify, step 2 (airplane mode):** `test_with_no_network_the_last_known_place_shows_with_its_age`,
  a 320 px phone signed in as the patient, through a router the test can kill:

  ```text
  the desk calls one patient: the page says number 3, and the phone keeps that state
  router cut: reload -> after the worker's 10 s, the offline page
  offline: T004 position 3, 'Last updated 05:28 · 10 s ago'   (the age counts up; nothing wider than 320 px)
  launch /t/?source=home-screen with no network -> the same ticket, T004
  router back, "Try again" -> the live ticket page
  ```

- [x] **How to verify, step 3 (a deploy):** `test_a_new_deploy_is_picked_up_on_the_next_launch` changes the
  version under the running server and navigates again:

  ```text
  worker '1.0.0-unknown' -> '9.9.9-next-deploy'
  caches ['clinicq-board-e2e', 'clinicq-patient-shell-1.0.0-unknown', 'clinicq-patient-tickets']
      -> ['clinicq-board-e2e', 'clinicq-patient-shell-9.9.9-next-deploy', 'clinicq-patient-tickets']
  ```

  The old shell is gone, while the board's cache and the kept tickets are untouched. That run was before
  `worker_version` stopped appending the default `GIT_SHA` of `unknown`; the full run above includes the change.
- [x] **How to verify, step 4 (Lighthouse):** Lighthouse **11.7.1** (the last version with a PWA category;
  12 removed it), mobile, against a ticket page on the development server with the migrated verification
  database:

  ```text
  lighthouse 11.7.1 PWA score 1
  PASS installable-manifest - Web app manifest and service worker meet the installability requirements
  PASS splash-screen - Configured for a custom splash screen
  PASS themed-omnibox - Sets a theme color for the address bar.
  PASS content-width - Content is sized correctly for the viewport
  PASS viewport - Has a `<meta name="viewport">` tag with `width` or `initial-scale`
  PASS maskable-icon - Manifest has a maskable icon
  MANUAL pwa-cross-browser, pwa-page-transitions, pwa-each-page-has-url (not scored)
  ```

- [ ] **How to verify, step 1 (install on Android):** not done; see above.
- [x] Browser, beyond the steps (`tests/e2e/patient/test_patient_app.py`, 4 passed):
  - the shell cache holds exactly the 17 listed files, and saving 12 tickets keeps 5, the newest first;
  - on a shared link and on the start page, a `beforeinstallprompt` is held (`defaultPrevented`) and no
    install box exists;
  - on the joining patient's own ticket, Chromium's `Page.getInstallabilityErrors` is `[]`, the box appears
    only once the offer arrives, and its button calls `prompt()` once and hides the box.
- [x] Integration (`tests/integration/queue/test_patient_app.py`, 5 passed):
  - the manifest's scope, start URL, name, theme and every icon's real PNG size;
  - the worker's headers, the filled-in version and shell, and a different worker after a version change;
  - every file the offline page loads is in the shell, and every shell file is served;
  - `/t/` redirects the owner to their open ticket and stops once it is cancelled;
  - `offer_install` is true only for the owner of an open ticket.
- [x] The existing patient browser tests (`test_ticket_page.py`, `test_push_permission.py`,
  `test_message_settings.py`) pass with the extended worker registered on every ticket page.

| Offline, 320 px | The install offer (the joining patient only) | Lighthouse 11.7.1 PWA |
|---|---|---|
| ![The offline page showing ticket T004, number 3 in line, last updated 10 seconds ago](https://github.com/Billykat7/clinicQ/blob/b793ae857a03771c33e2e282d92dbd062889c28a/docs/GITHUB/PR/M9/assets/pr69/offline-last-known.png?raw=true) | ![The Add to home screen box on the ticket page](https://github.com/Billykat7/clinicQ/blob/b793ae857a03771c33e2e282d92dbd062889c28a/docs/GITHUB/PR/M9/assets/pr69/install-offer.png?raw=true) | ![Lighthouse PWA category with every automated check passing](https://github.com/Billykat7/clinicQ/blob/b793ae857a03771c33e2e282d92dbd062889c28a/docs/GITHUB/PR/M9/assets/pr69/lighthouse-pwa.png?raw=true) |

## Acceptance criteria

- [ ] **The app installs to an Android home screen and launches standalone:** not checked on a phone. What
  was checked: the manifest says `standalone` with a `start_url` inside its scope, Chromium's installability
  check reports no errors on a ticket page, and Lighthouse's installable audit passes.
- [x] **With no network, the last known ticket position is shown with its age:** the router dies, and the
  ticket page and the home-screen launch both show T004, number 3, "Last updated 05:28 · 10 s ago", counting up.
- [x] **A new deploy is picked up on the next launch without manual intervention:** the version changes under
  a running server, the next navigation installs the new worker, and the old shell is deleted.
- [x] **The install prompt appears only after a successful join:** the browser's offer is held on a shared
  link and the start page with no box shown, and offered to the joining patient on their own ticket.
- [x] **Lighthouse PWA checks pass:** Lighthouse 11.7.1, PWA score 1, every automated audit passing.
- [x] **The cache is bounded and cannot grow without limit:** the shell is exactly its 17-file list, and 12
  saved tickets leave 5.

## Risk and rollback

- **A broken shell list would stop the worker installing, and web push with it.** The integration test
  fetches every listed file.
- **Pages are never served from the cache while the network answers.** The offline page appears only after
  a failure or 10 silent seconds, and it says the numbers are old.
- **The worker now handles `fetch` for `/t/` pages.** Streams, JSON and API calls are not intercepted.
- **No migration.**
- **Rollback** is a revert. Browsers pick up the previous worker on their next navigation, because its bytes
  differ. That worker has no cache code, so the last shell cache and the kept ticket states stay on phones
  that installed this one, unread, until the site's data is cleared.

Closes #69
