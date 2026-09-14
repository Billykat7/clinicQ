# Release v0.8.0: Waiting-room Display Monitor

**Date:** 2026-09-15 · **Milestone:** M8 · **Issues closed:** 56–62

A pre-release. v0.7.0 gave staff the screen they run the day from, but a call was still seen only on
staff screens. This release is **the TV in the waiting room**. It covers:

- a board of every open queue, with its now-serving and up-next numbers, readable across the room;
- updates within a fraction of a second of *Call next*;
- a chime and the number and room said aloud;
- screens a clinic pairs with a six-character code;
- a board that keeps its last numbers and says how old they are when the clinic's internet fails.

The patient's own ticket page and messages (M9) are still to come.

Three rules shape the release, and each is enforced where a bug cannot get round it:

- **The board is given only what it may show** (non-negotiable 4). Every board response, the page, its
  JSON, its stream and its announcements, is built by one projection. Under `number_only` a name or reason
  key does not exist in the payload. A template, a stream or the speaking script handed anything else
  refuses before it renders, sends or speaks.
- **The address is not a key.** Only a kiosk box paired with the clinic, or the clinic's own staff, sees a
  board. The same address anywhere else shows a pairing code.
- **The screen never pretends to be current.** A lost connection says *Reconnecting*. A board with nothing
  from the clinic for 20 seconds says *Not up to date* and gives the time of its last update.

All seven pull requests merged between 14 and 15 September 2026, each after its checks were green and
before the next branched from `main`: #178 (58) → #179 (56) → #180 (57) → #181 (59) → #182 (61) →
#183 (60) → #184 (62). The tag is cut from `main` after #184 merges.

## What shipped

- **One privacy projection for every board** (Issue 58, PR #178).
  - `project_board()` in `src/modules/display/projection.py` is the only reader of a clinic's tickets for
    a board. It returns plain, frozen dataclasses, and `BoardState.payload()` is the JSON.
  - **Under `number_only`** the `name` and `comment` keys are absent, not `null`.
  - **Consent is read when the board is built**, through `has_consent()`, so withdrawing it takes the
    name off the next update.
  - **A reason needs four things:** `full` mode, the clinic's switch, the patient's standing consent and
    this visit's consent.
  - **Guards:** `ensure_projected()` refuses raw data in a template or a stream, and
    `ensure_payload_private()` refuses a payload with a key its mode forbids. Source guards fail on a
    template rendered around the guard or a ticket read outside the projection. A sweep searches every
    `/display` route the application serves for a seeded name.
- **The kiosk board page** (Issue 56, PR #179).
  - `GET /display/{site_id}`: every open queue's newest call, large, with its status. Earlier calls
    still in rooms, up to five numbers up next, and the waiting count.
  - **Layouts:** one, two and three queues side by side, four in a grid, and more than four in pages of
    four that turn every 15 seconds. A clock, the clinic's name, and one health notice at a time.
  - **Legibility at 5 metres, measured in Chromium:** on a 32-inch screen, a served number is at least
    49.6 mm tall in every layout and an up-next number at least 22.1 mm.
  - **A new call** is shown by inverted colours, a heavy border and "▶ Called now", and is said once to
    assistive technology.
  - Its own stylesheet and scripts, with no inline code under the CSP.
- **The live stream** (Issue 57, PR #180).
  - `GET /display/{site_id}/stream` sends the whole board first on every connection, so every
    reconnection is a full resync, then the board after each call, queue change or settings change. A
    call reached the board **33–57 ms** after its commit.
  - **Reconnection:** a heartbeat every 15 seconds. Two missed beats show "⟳ Reconnecting to the
    clinic…". Retries back off with jitter up to 30 seconds, and after three failures the board also polls
    `/state`.
  - **Across instances:** with `REDIS_URL`, events fan out over Redis pub/sub (`LIVE_EVENTS_FANOUT`).
  - **Streams that vanish without closing are noticed at the next beat.** A simulated eight-hour day
    left none open. `clinicq_live_streams_open` is on `/metrics`.
- **Accessibility and themes** (Issue 59, PR #181; migration `0028`).
  - **Three themes, chosen per clinic** (`dim`, `bright`, `high_contrast`). Every text on every board
    state reaches 4.5:1, checked from the stylesheet and as Chromium draws it.
  - **Every status has a shape and words** as well as a colour, checked under greyscale emulation.
  - **Reduced motion** replaces the pulse with a still ring.
  - **Findings for the accessibility audit (Issue 101)** are in
    `docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md`.
- **Kiosk screens: pairing, removal and silence alerts** (Issue 61, PR #182; migration `0029`).
  - **Pairing:** every box opens `/display` and shows a code, valid for ten minutes. The manager types it
    under **Clinic settings → Display boards**, and the box opens its board by itself (2.6–3.1 s).
  - **The box's secret** is kept in an httpOnly, same-site cookie and stored only as its SHA-256, like a
    refresh token.
  - **Removal:** a removed box is refused at once and goes back to a code at its next access check
    (within 30 s).
  - **Silence alerts:** each box reports every minute. A box silent for 10 minutes posts one message
    naming it and its clinic to the team channel of Issue 14, and one more when it is back.
  - **Operators** see every screen at `/admin/display-devices`.
  - **`docs/OPS/KIOSK_SETUP.md`** is the provisioning guide.
- **Call announcements** (Issue 60, PR #183; migration `0030`).
  - **What is said:** a chime, then *"Number A 0 1 2, please go to Room 4"*, in the clinic's language.
    Calls are said one at a time.
  - **Never a name.** Sentences have blanks for the number and the room only, and the speaking script is
    given nothing else. A browser test proves it with full names on the screen.
  - **Settings:** mute (`announce_audio`) and volume (`announce_volume`) per clinic. Muted, the screen
    still highlights every call.
  - **How it is said:** browser speech with a voice for the language; otherwise recorded clips of each
    character; otherwise English; otherwise the chime alone.
  - **The chime** is generated from sine waves by `scripts/audio/make_chime.py`.
  - The board's pages may autoplay their own audio (`Permissions-Policy: autoplay=(self)` under
    `/display`); every other page keeps `autoplay=()`.
- **Resilience** (Issue 62, PR #184).
  - **The last board stays, marked as old.** A paired box keeps the last board it received. After 20
    seconds with nothing from the clinic, a banner replaces the health notice: "⚠ Not up to date. Last
    updated at 14:32.", in the clinic's time.
  - **Back within 30 seconds.** Measured after a dead router (silent, no errors): 3.3 s after a
    94-second outage, and 0.8 s after a server restart. `board-live.js` now gives up a reconnection attempt that
    neither opens nor fails, and a poll after 8 seconds. A successful poll starts the stream at once.
  - **A power cut with no network at boot.** A service worker (`/display/board-sw.js`, scope `/display`)
    keeps the board page and its files. The box's start address opened the last board from its own cache
    in 1.1 s, with the banner, and was current 1.1–1.4 s after the network returned.
  - **Limits on what is kept.** A kept board older than four hours is hidden, and a replayed call is never
    announced again. A box that shows its pairing code drops everything it kept. A staff preview keeps
    nothing.
  - **The resilience suite** (`tests/e2e/display/test_board_resilience.py`) covers a dead router, a
    server restart, a slow link (1.5 s latency, 200 kbit/s), a power cut with no network, a removed box
    rebooting offline, and a day offline. It relays traffic through `tests.e2e.conftest.Router`, which can
    go silent like a dead router.

## Migrations

Three revisions, `0028` to `0030`, applied in order by `scripts/db/deploy-sequence.sh`. None rewrites
existing data on the way up, and each is reversible.

- **`0028_site_board_theme`**: adds `site.board_theme` (non-null, server default `dim`, the only look
  boards had before). The downgrade drops the column and loses only each clinic's choice.
- **`0029_display_devices`**: creates `display_device` with its unique token-digest and code-digest
  indexes and a clinic index. **The downgrade drops the table and unpairs every box**; each must be
  paired again after upgrading back.
- **`0030_site_announce_volume`**: adds `site.announce_volume` (non-null, server default 80) and its check
  constraint `ck_site_announce_volume` (0 to 100). The downgrade drops both and loses only each clinic's
  volume.

## Upgrade notes

- **Board addresses are no longer public.** Before this release, anyone with `/display/{site_id}` saw
  that clinic's numbers. Now a browser that is not a paired box of the clinic, or its signed-in staff,
  is sent to the pairing screen, and the JSON and stream answer `401`. **Pair every screen once** under
  **Clinic settings → Display boards** (Part B of `docs/OPS/KIOSK_SETUP.md`, two minutes each).
- **Boards make sound by default.** `announce_audio` has defaulted to on since v0.4.0. A clinic that does
  not want sound unticks it, and a TV's own volume still applies. Kiosk Chromium must run with
  `--autoplay-policy=no-user-gesture-required` (KIOSK_SETUP A5).
- **The board's service worker needs a secure origin.** It registers over HTTPS or on `localhost`. A
  staging environment served over plain HTTP gets no offline shell. Everything else works.
- **Seven new settings**, all with defaults (`.env.example` regenerated): `BOARD_HEALTH_TICKER` (true),
  `LIVE_EVENTS_FANOUT` (true), `DISPLAY_DEVICE_SILENT_MINUTES` (10), `DISPLAY_PAIRING_CODE_MINUTES` (10),
  `DISPLAY_DEVICE_COOKIE_NAME` (`clinicq_display`), `TEAM_WEBHOOK_URL` (empty: alerts are only logged)
  and `TEAM_WEBHOOK_KIND` (`slack` or `discord`). Run `make check-config` after copying.
- **One new scheduler job:** `display_device_watch`, every minute under advisory lock 661. It posts silence
  alerts and deletes boxes that never paired, a day after their code expired.
- **New routes:**
  - board pages under `/display`: the start page, `/pairing`, `POST /heartbeat`, `/board-sw.js`, and per
    clinic the page, `/state` and `/stream`;
  - `GET`/`POST /api/v1/sites/{site_id}/display-devices`, `PATCH …/{device_id}`,
    `POST …/{device_id}/revoke` and `GET /api/v1/display-devices`, all in `contracts/sites.yaml`;
  - the dashboard's **Display boards** tab and the operators' `/admin/display-devices/{all,silent,removed}`.
- **Display settings gain `board_theme` and `announce_volume`**, both optional on input. A client that
  sends neither keeps the clinic's values.
- **Put the board's stream behind a proxy that does not buffer it,** with an idle timeout above 15 seconds.
  The stream sends `X-Accel-Buffering: no`.
- **The spec's dependency list for Issue 60 was corrected** in `docs/GITHUB` (27, not 26, plus 58). The
  GitHub issue bodies are generated from those files by `scripts/gh_sync_docs.py`, which has not been run.

## Known issues

- **Nothing has been checked by the people these checks need.** Each is left open, with its procedure
  and a place to record it:
  - **The five-metre reading** on a real 32-inch screen, with real readers (Issues 56, 59): sizes are
    measured in a browser against published thresholds only (`docs/OPS/BOARD_LEGIBILITY.md`).
  - **A fluent speaker's check of each announcement language**, English included (Issue 60). The five
    sentences are drafts written by an AI assistant, and `checked_by` is empty for all of them
    (`docs/OPS/BOARD_AUDIO.md`).
  - **A person following `KIOSK_SETUP.md`** who did not write it (Issue 61). An independent AI agent
    followed the no-hardware practice run and Part B, and its nine findings were fixed. No person has
    followed it, and Part A has not been done on a Raspberry Pi or a small PC.
- **No real kiosk box has run this release.** Pairing, removal, the power cut, autoplay, speech and the
  service worker were tested in Playwright's Chromium on a developer's Mac, not in Raspberry Pi OS
  Chromium on a TV. Nobody has listened to an announcement in a room.
- **No number recordings exist,** and the one machine whose voices were listed (macOS Chromium) has
  English voices only. Without recordings or a voice for the language, an isiZulu, isiXhosa, Sesotho or
  Afrikaans clinic's board speaks English.
- **The wall-clock soak ran an earlier build.** A real eight-hour run of the board (headless Chromium, a
  call every three minutes) was started on #56's board, which polled before #57's stream existed. Its
  result is below under *Verification*. The final board, with the stream, announcements and offline
  keeping, was soaked for eight hours only on the page's clock.
- **A kept board can hold names on the box.** Under a name mode, the last board a box kept, including
  names patients agreed to show, stays in the box's browser storage for up to four hours, or until the
  box shows its pairing code. A consent withdrawn while the box is offline reaches the screen when it
  reconnects.
- **A box that never showed its board online cannot start offline.** The offline shell exists only after
  a first successful load, and a box removed since then has none.
- **Removal takes up to 30 seconds** to reach an open board: the stream's access check.
- **The per-instance limit of 100 streams** is shared by a clinic's boards and dashboards.
- **Health notices are English only** until the translation work (Issue 77).
- **The clinic settings tabs overflow sideways at phone width** (390 px): the breadcrumb trail is wider
  than the screen. This predates M8 and is flagged for its own fix.
- **Everything from v0.7.0's list that M8 did not touch still stands.**
  - Nothing reaches a patient's phone yet (M9).
  - The notification bell answers 403 for clinic roles.
  - The consent wording is a draft for the M13 review.
  - The credential in the repository's history is still not rotated, and nothing is provisioned.
  - **Only `v0.2.0` has ever been tagged**: `v0.1.0` and `v0.3.0`–`v0.7.0` have release notes but no
    tags, and they should be cut in order before this one.

## Verification

Run on the Issue 62 branch based on `main` after #183, which is `main` as #184 will leave it. It used
PostgreSQL 18 + PostGIS and Redis in Docker, both required rather than skippable, and Playwright's
Chromium:

```text
TZ=UTC pytest -q -n auto --dist loadscope tests
                                      2070 passed, 1 skipped (the rush), 9 xfailed in 445 s
tests/e2e (45 browser tests)          under -n auto: 45 passed
the resilience suite, 8 h offline     7 passed (BOARD_OFFLINE_SOAK_HOURS=8)
ruff check . / ruff format --check    clean
mypy src/                             clean (274 files)
```

CI ran every shard on every pull request, including the browser shard, and each merged with every check
green. Each pull request carries its own evidence, and it is worth reading beside the suite:

- **#178:** the sweep of every `/display` route for a seeded name under each mode and viewer; each of a
  reason's four conditions shown necessary; and the guards failing on the shapes they exist to catch.
- **#179:** each layout at 1080p and 720p with the millimetre sizes, the new-call highlight with and
  without reduced motion, and the eight-hour page-clock run.
- **#180:** 33–57 ms from commit to screen, a server restart and a ten-minute outage healed with no reload,
  a missed heartbeat noticed between 30 and 31 s, and streams that stay flat over a simulated day.
- **#181:** every theme in colour, greyscale and deuteranopia, the contrast table, and the manager choosing a
  theme on a live board.
- **#182:** pairing in 3.1 s through the dashboard, removal, a power cut in software, the silence alert's
  messages, the operators' console, and the independent agent's report on the guide.
- **#183:** chime then sentence in order, two calls one after the other, no name with full names on the
  screen, mute keeping the highlight, clips without speech, isiZulu and its English fallback, and two
  calls said by the Mac's real speech engine on the dev database.
- **#184:** the banner after a dead router, recovery times, the offline boot from cache, the removed box
  that forgets, a slow link, and the day-offline runs.

### The soaks

- **Eight hours on the page's clock, online** (#56's test, run in CI with the final board, stream,
  announcer and offline keeping): 160 calls, heap 2.05 → 2.14 MB, 600 DOM nodes and 46 listeners before and
  after, and the layout unchanged. It found the announcer holding every sentence in a browser with no
  voice (listeners 66 → 382), which #183 fixed before merging.
- **Eight hours on the page's clock, offline** (#184, with a dead router): 580 reconnection attempts. The
  DOM, the listeners and the layout were unchanged. The heap grew 10.7 %, which heap snapshots trace to
  DevTools' network buffer and console, not to the board's own objects (+6 KB in three hours).
- **Eight hours on the wall clock, online**, on #56's polling board (started before #57), a call every
  three minutes:

**Still running when this was written** (it ends at about 03:53 on 15 September). This section is updated with the final measurement before the pull request merges. The measurements so far:

| When | Calls | JS heap (B) | DOM nodes | Elements | Listeners | Panels moved |
|---|---|---|---|---|---|---|
| 2026-09-14 20:23 | 11 | 1,841,784 | 465 | 126 | 35 | no |
| 2026-09-14 21:23 | 31 | 1,887,784 | 465 | 126 | 35 | no |
| 2026-09-14 22:23 | 51 | 1,890,652 | 465 | 126 | 35 | no |
| 2026-09-14 23:23 | 71 | 1,891,932 | 465 | 126 | 35 | no |
| 2026-09-15 00:08 | 86 | 1,881,800 | 369 | 85 | 35 | no |

16 measurements so far; heap 1,841,784–1,892,456 B, first 1,841,784 → last 1,881,800 B (+2.2 %); listeners [35]; panel rectangles identical in every measurement.

The drop in DOM nodes at midnight is the new service day: numbers restart at 001, and the waiting lines are shorter.
