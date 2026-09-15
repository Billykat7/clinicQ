# The patient app: installing, offline, updates

ClinicQ's ticket pages are also an installable web app (Issue 69). This page says what a patient gets, what
each release does to an installed app, and what has and has not been checked.

## 1. What a patient gets

- **Installing.** After joining a queue, the patient's own ticket page offers **Add to home screen** once
  the browser says it can install. The browser's own install banner is always held back, so nobody is asked
  the first time they open a link, and a family member following a shared link is never asked. "Not now" is
  remembered on that phone. On an iPhone there is no install offer to show: the box says to use Share, then
  Add to Home Screen.
- **Opening.** The app opens standalone at `/t/`, which goes straight to the signed-in patient's open ticket
  today, or else to the last unfinished ticket that phone followed, or else says how to join a queue.
- **No signal.** Every state the ticket page shows is kept on the phone. When a `/t/` page cannot be reached
  (no answer within 10 seconds), the app shows the offline page: the ticket number, the place in line the
  phone last saw, when it was last updated and how long ago, counting up, and a clear "your place may have
  moved since". It reloads into the live ticket when the phone is back online: on the browser's online
  event, on "Try again", or by itself within 15 seconds of the server answering again.

## 2. What is stored on the phone, and how much

All in the browser's Cache Storage, under names starting `clinicq-patient-`; the waiting-room board's
caches (`clinicq-board-`) are never touched.

| Cache | Holds | Bound |
|---|---|---|
| `clinicq-patient-shell-<release>` | The offline page and exactly the files it needs (`PATIENT_SHELL` in `src/web/ticket.py`) | The list: about 17 files. A test fails if the offline page needs a file not on it |
| `clinicq-patient-tickets` | The last state of each ticket followed: number, clinic, queue, place in line, status | At most 5 tickets, none older than 18 hours; pruned on every save |

Nothing about the patient is kept: no name, no phone number.

## 3. What a release does to an installed app

`/patient-sw.js` is served with the release's version (and commit, when the image sets `GIT_SHA`) written
in, and is never cached by the browser. Every page in the app asks the browser to check it on load (the
browser's own checks are throttled), so the first launch after a deploy installs the new worker, which takes over at once, keeps the new release's shell and deletes
the old one. Pages are always fetched from the network first, so the page itself is the new release on that
same launch. Nobody has to clear anything.

**In development**, the version does not change between edits. The shell's files are served from the cache
and refreshed behind it, so an edited CSS or JavaScript file shows on the second reload of a `/t/` page, or
straight away with DevTools' "Update on reload".

## 4. Checked, and not checked

- **Lighthouse 11.7.1's PWA category passes** (installable manifest and service worker, splash screen,
  themed address bar, content width, viewport, maskable icon). Lighthouse 12 removed the PWA category, so
  the check pins 11.7.1:
  `CHROME_PATH=<chromium> npx lighthouse@11.7.1 http://127.0.0.1:8000/t/<token> --only-categories=pwa --form-factor=mobile`.
- **Chromium's own installability check** (`Page.getInstallabilityErrors`) reports nothing on a ticket page,
  in `tests/e2e/patient/test_patient_app.py`, with the offline page, the update and the cache bound.
- **Not yet checked on a real Android phone**: installing from Chrome and launching standalone from the home
  screen. Record the result here when it is done: phone, Android and Chrome versions, date, who checked.
- **Not checked on an iPhone.** Add to Home Screen should work from Safari's share menu; WebKit documents
  Home Screen web apps as keeping their own storage, apart from Safari's, so an iPhone app may open at "No
  open ticket on this phone" until the patient signs in inside it.

---

**Refs:** [Issue 69](../GITHUB/ISSUES/M9/ISSUE_69_pwa_shell_service_worker.md) · `src/static/patient-sw.js` ·
`src/static/manifest.json` · `src/web/ticket.py` · [WEB_PUSH.md](WEB_PUSH.md)
