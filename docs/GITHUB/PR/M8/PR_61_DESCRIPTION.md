# PR: Kiosk screens paired with a code, removed at once, and watched for silence (Issue 61 / M8-61)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#61](https://github.com/Billykat7/clinicQ/issues/61) · **Builds on:** #23 (sites), #56 (the board
page, PR #179), #57 (the live stream, PR #180) and #14 (the team channel) · **Unblocks:** #62 (resilience),
#106 (the pilot kit)

A waiting-room box is installed once and then ignored for months. This PR makes it something a clinic can
plug in without typing anything into it, and something the team hears about from the office when it goes
dark:

- **Pairing with a code.** Every box opens `/display` and nothing else. An unpaired box shows a
  six-character code in board-sized type. The clinic manager types it into **Clinic settings → Display
  boards**, and the box opens its board by itself: **2.6–3.1 s** from the code appearing, in the browser test
  and on the dev database.
- **A board address is no longer a key.** Before this PR, anyone with a clinic's board URL saw its numbers.
  Now a board, its state and its stream are shown only to a **paired screen of that clinic** or to the
  clinic's **own staff**. Anything else gets the pairing screen (page) or a `401` (state, stream).
- **Removing a screen works at once.** Its secret stops working on the next request. Its open stream closes at
  the next access check (30 s), and the box goes back to a new pairing code by itself: **26–28 s** on the dev
  database, 1.7 s in the browser test (where the check runs every 2 s).
- **Silence is reported.** Each box reports every minute. A paired box not heard from for **10 minutes** posts
  one message to the team channel naming the screen and its clinic, and one more when it is back.
- **The operator's console** at `/admin/display-devices` lists every screen on the platform, with its clinic,
  status, last heartbeat and version.
- **`docs/OPS/KIOSK_SETUP.md`** is the provisioning guide: auto-login, kiosk flags, starting on boot and after
  a power cut, no screen blanking. It was followed by an independent agent that had not written it (see
  *The guide, followed by someone else*). **No human has followed it yet**, so that criterion stays open.

## Summary

- **The device record** (`src/database/models/display_device.py`, migration `0029`):
  - the clinic, a label, the queues it shows (`null` for all of the clinic's open queues);
  - `token_hash`, the SHA-256 of the box's secret, the same hashed-secret pattern as refresh tokens
    (`hash_refresh_token`), unique;
  - `pairing_code_hash` and `pairing_expires_at`: the code is stored only as its digest too;
  - `paired_at/by`, `revoked_at/by`, `last_seen_at`, `app_version`, `user_agent`, `silent_alerted_at`.
- **The box's side** (`src/web/display.py`):
  - `GET /display`: a paired box is redirected to its board; any other box gets a device (or a fresh code
    for its existing one) and the pairing page. The secret goes into an `HttpOnly`, `SameSite=Strict`
    cookie on path `/display`, kept for 400 days, `Secure` outside development;
  - `GET /display/pairing`: `waiting`, `expired` or `paired` with the board's address, polled every 3 s
    by `board-pair.js`;
  - `POST /display/heartbeat`: every 60 s from `board-device.js`, recording version and browser. A `401`
    sends the box back to `/display`; a newer version reloads it at a quiet moment (no call on screen);
  - the board page, `/state` and the stream decide their audience with `board_audience`: a paired device of
    the clinic (`DEVICE`, limited to its queues), staff with a role at the clinic (`STAFF`), or nobody.
- **The manager's side**:
  - API (`src/modules/display/router.py`, documented in `contracts/sites.yaml`):
    `GET/POST /sites/{site_id}/display-devices`, `PATCH …/{device_id}`, `POST …/{device_id}/revoke`.
    Changing needs `sites.display` at `update` (the manager, the grant that already governs the board's
    settings); reading needs `read` (the front desk). Each change is audited;
  - screen: the **Display boards** tab (`settings_devices.html`): pair form, filter, sortable list, and a
    panel to rename, change queues or remove, with **Remove this screen** in the panel's foot so it is never
    below the fold.
- **The operator's side:** `GET /api/v1/display-devices` and `/admin/display-devices/{all,silent,removed}`,
  gated on the business-tier `sites` read grant, like the verification console.
- **The watch** (`src/core/scheduler.py`, job `display_device_watch`, every minute under an advisory lock):
  `watch_devices` alerts once per silence and once when a box is back; `purge_unpaired` deletes boxes that
  never paired, a day after their code ran out.
- **The team channel** (`src/core/team_alerts.py`): `post_team_alert` posts to #14's incoming webhook
  (`TEAM_WEBHOOK_URL`, `TEAM_WEBHOOK_KIND` slack or discord) in the same shapes as
  `scripts/cd/notify_deploy.py`. It logs every alert at WARNING, and never raises.
- **Settings:** `DISPLAY_DEVICE_SILENT_MINUTES` (10), `DISPLAY_PAIRING_CODE_MINUTES` (10),
  `DISPLAY_DEVICE_COOKIE_NAME`, `TEAM_WEBHOOK_URL`, `TEAM_WEBHOOK_KIND`; `.env.example` regenerated.

## Design notes

**Why a cookie and not a token in the URL.** The spec asks for a board address that is "not guessable or
shareable". A secret in the URL is shareable by definition: it is in the browser history, in screenshots
and in anything the box's address bar is copied into. The cookie is `HttpOnly` and scoped to `/display`, so
the board's scripts cannot read it and no other page is sent it. A kiosk browser's profile keeps it through
reboots, which the power-cut test checks.

**Why the board URL stopped being public.** #56–#59 let anyone open `/display/{site_id}` and capped them at
`number_only`. With paired devices, the clinic's own screens and staff are the only audience a board has,
so the anonymous path is closed instead of kept as a second, weaker door. The privacy projection (#58) is
unchanged: a device sees the clinic's display mode, and a stranger sees nothing. The integration tests
that search every board endpoint for a seeded name now also cover the device, the pairing page and the
refusals.

**Only digests are stored.** A database dump reveals neither a box's secret nor a live code. A wrong,
expired or used code gets the same `404` and the same words, so the answer does not help anyone guess.
Codes use the 31 unambiguous characters of ticket references (no `0/O`, `1/I/L`); case, spaces and dashes do not matter.

**Why a watch in the app, not a Gatus probe.** #14's Gatus probes URLs from outside. It cannot know that a
box in Hillbrow stopped calling in; only the application sees the heartbeats. So the application posts to
#14's channel itself, in #14's message shapes, and the runbook gains an entry. The watch runs under an
advisory lock, so several instances send one alert.

## The guide, followed by someone else

The criterion asks for a person who did not write `KIOSK_SETUP.md` to follow it. **No human has.** What was
done is the closest honest substitute: **an independent Claude agent** was given only the guide and the
address of a running ClinicQ, not the code or the tests. It followed *Trying it without a box* and Part B,
with a manager signed in a separate browser. Its report:

- **Paired:** 6.45 s from the code appearing to the board, most of it clicking through the dashboard; the box
  took 2.44 s after **Pair**. It typed the code in lower case with a dash, as the guide allows.
- **Power cut:** the box's browser closed, 10 s wait, relaunched with the same profile: the board within
  about 1 s, nobody touching it.
- **Removal:** back to a pairing code 21.8 s and 26.9 s after confirming, both within the guide's
  "half a minute".
- **A board address opened in another browser:** the pairing screen.
- **Not done by it:** Part A (no Pi, TV or Ubuntu box), a real kiosk window (it ran headless), sound, and the
  10-minute alert.

It found nine unclear points. All are fixed in this PR:

| What it found | Fixed |
|---|---|
| The guide said a code changes every ten minutes; the screen said "a few minutes" | The screen now says "This code works for 10 minutes", from the setting, and the guide matches |
| **Clinic settings** opens on *Profile*, and *Waiting-room screen* sits next to *Display boards* | The guide names the tab and says what the similar one is for |
| No word on what the manager sees after **Pair** | The guide says: the page reloads, and the screen is listed as *Showing the board* |
| **Remove this screen** was below the fold in the panel; the confirmation and the *Removed* rows were not mentioned | The button moved to the panel's foot; the guide describes the panel, the confirmation, and that removed screens stay listed |
| The practice run gave no sign-in details | It points to the seeded manager (`make seed-dev-data`, QUICKSTART) |
| Only Google Chrome commands | Chromium on macOS and Linux added |
| "#62's offline behaviour" means nothing to a newcomer | Replaced with what the board does |
| "Operators" undefined; a manager gets *Access denied* | Defined, and the denial said to be expected |
| `--disable-infobars` is ignored by recent Chrome | Removed from the service |

## Changes

- **New:**
  - `alembic/versions/0029_display_devices.py`, `src/database/models/display_device.py`;
  - `src/modules/display/devices.py`, `src/modules/display/router.py`, `src/core/team_alerts.py`;
  - `src/templates/display/pair.html`, `src/templates/dashboard/settings_devices.html`,
    `src/templates/admin/display_devices.html`;
  - `src/static/js/board-pair.js`, `board-device.js`, `admin-display-devices.js`;
  - `docs/OPS/KIOSK_SETUP.md`;
  - `tests/integration/display/test_display_devices.py`, `tests/e2e/display/test_board_devices.py`;
  - `docs/GITHUB/PR/M8/assets/pr61/*.png`.
- **Enums and settings:** `DisplayDeviceStatus`, `TeamWebhookKind`, `AuditEntityType.DISPLAY_DEVICE`,
  `PairingState`; the five settings above.
- **Board:**
  - `src/web/display.py`: the audience;
  - `display_stream.py`: the audience, re-checked at every access check;
  - `board_state.py`: projections keyed by queue selection;
  - `board-live.js`: `checkAccess`, and `leave()` on a `401`;
  - `board.html`, `board.css`.
- **Dashboard and admin:** `src/web/dashboard/settings.py` (the tab), `src/web/routes.py` (the console),
  `src/api/v1/router.py`, `src/core/scheduler.py`, `contracts/sites.yaml`.
- **Tests updated:**
  - the site-scope guard (`_UNSCOPED_BY_DESIGN`: the secret lookup, the code lookup, the watch and the
    purge read across clinics on purpose);
  - the cross-tenant case `displaydevice`;
  - the board privacy, page and stream tests, now as a paired device;
  - manager settings tabs.
- **e2e:**
  - the day fixtures retry a `TRUNCATE` that PostgreSQL cancels as a deadlock (`empty_tables`), which
    flaked once in a full run;
  - the display fixtures gain a manager and a paired-box helper.
- **Docs:**
  - `docs/CICD/RUNBOOK_ALERTS.md` (*A waiting-room board is silent*), `infra/monitoring/README.md`;
  - `docs/PRODUCT/04-display-monitor.md`, `docs/PRODUCT/07-devices-and-bom.md`;
  - progress: M8 status, `docs/GITHUB/README.md`, `README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (273 files)
- [x] `make milestone-progress-check ARGS='--assume-closed 61'`: 14 milestones up to date
- [x] Whole suite, in UTC with PostgreSQL and Chromium, and the Redis- and PostgreSQL-marked integration tests
  with Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… pytest -q --no-cov -n auto
  2019 passed, 13 skipped, 9 xfailed in 216.66s
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest -q --no-cov -n auto -m "redis or postgres" tests/integration
  161 passed, 1 skipped in 119.59s
  ```

  The 13 skips in the first run are the Redis tests (covered by the second run) and the 07:30 rush, which
  runs alone.

- [x] The new tests:

  ```text
  tests/integration/display/test_display_devices.py::test_a_fresh_box_pairs_with_a_code_typed_by_the_manager_and_opens_its_board_by_itself
  tests/integration/display/test_display_devices.py::test_the_secret_is_stored_only_as_its_digest_and_a_bad_code_learns_nothing
  tests/integration/display/test_display_devices.py::test_a_box_shows_only_its_own_clinics_board_and_only_the_queues_it_was_given
  tests/integration/display/test_display_devices.py::test_a_removed_box_is_refused_at_once_and_offered_a_new_code
  tests/integration/display/test_display_devices.py::test_the_heartbeat_keeps_the_record_current_and_refuses_another_site
  tests/integration/display/test_display_devices.py::test_a_box_silent_for_ten_minutes_raises_one_alert_naming_it_and_its_clinic_and_one_when_back
  tests/integration/display/test_display_devices.py::test_the_operator_sees_every_screen_on_the_platform_and_a_clinic_manager_does_not
  tests/integration/display/test_display_devices.py::test_unpaired_boxes_are_tidied_away_after_a_day
  tests/integration/display/test_display_devices.py::test_an_alert_reaches_the_team_channel_in_each_services_shape_and_a_dead_channel_is_not_an_error
  tests/integration/display/test_display_devices.py::test_the_clinic_settings_list_and_the_operator_console_show_the_screens_they_should
  tests/e2e/display/test_board_devices.py::test_a_fresh_box_pairs_in_under_two_minutes_with_no_address_typed
  tests/e2e/display/test_board_devices.py::test_a_removed_screen_leaves_the_board_by_itself_for_a_new_pairing_code
  tests/e2e/display/test_board_devices.py::test_a_board_address_opened_in_a_browser_that_is_not_a_paired_screen_shows_no_board
  tests/e2e/display/test_board_devices.py::test_after_a_power_cut_the_box_returns_to_its_board_with_nobody_touching_it
  ```

  ```text
  paired and showing the board 3.1 s after the code appeared
  removed screen back on its pairing code 1.7 s after Remove
  4 passed in 12.00s
  ```

- [x] **The silence alert** (the watch run against a box whose last heartbeat was set 11 minutes back): one
  message, no second one a minute later, and one when the next heartbeat arrives:

  ```text
  📺 Waiting-room board silent: “TV by reception” at Zola Clinic has not been heard from since …
  ✅ Waiting-room board back: “TV by reception” at Zola Clinic is reporting again.
  ```

  Posted to a local HTTP server as `{"text": …}` (Slack) and `{"content": …}` (Discord); an unreachable
  channel returns `False` and logs the alert.

- [x] **On the dev database** (Hillbrow Community Health Centre), with a fresh headless Chromium as the box
  and the seeded manager in a second browser, through the dashboard's own form:

  ```text
  code G55 GFE | This code works for 10 minutes. After that, a new one appears here.
  paired in 2.6s from code shown
  admin http://127.0.0.1:8019/admin/display-devices/all
  removed -> code in 26.0s (server default access check 30s)
  ```

- [ ] **A physical box (Raspberry Pi or small PC) set up with Part A:** not done; no hardware was available.
  The power-cut behaviour is tested in software (the browser's stored state, reopened).
- [ ] **A person who did not write the guide:** not done (above). An independent agent followed it.

### Screenshots

The pairing screen on an unpaired box (1920×1080):

![Pairing code](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/box-pairing-code-1920x1080.png?raw=true)

The clinic manager's **Display boards** tab, before pressing Pair, and after, with the screen listed:

![Pair form](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/manager-pair-form-1366.png?raw=true)

![Listed](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/manager-display-boards-list-1366.png?raw=true)

The same box a few seconds later, on its own:

![Board after pairing](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/box-board-after-pairing-1920x1080.png?raw=true)

A screen opened, with **Remove this screen** in the panel's foot, and the confirmation:

![Screen opened](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/manager-screen-opened-1366.png?raw=true)

![Remove confirmation](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/manager-remove-confirm-1366.png?raw=true)

After removal: the list keeps the screen as *Removed*, and the box is back on a new code by itself:

![After removal](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/manager-after-removal-1366.png?raw=true)

![Box after removal](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/box-back-to-code-after-removal-1920x1080.png?raw=true)

The operator's console, with a screen opened:

![Operator console](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/admin-display-devices-1366.png?raw=true)

The tab at phone width (390 px). The breadcrumb trail overflows on every settings tab at this width, which
predates this PR and is tracked separately:

![Phone width](https://github.com/Billykat7/clinicQ/blob/aeb21a4b552ddbec91badfb78a0695b08b0b83f1/docs/GITHUB/PR/M8/assets/pr61/manager-display-boards-390.png?raw=true)

## Acceptance criteria

- [x] A new box pairs with a code in under 2 minutes and needs no URL typed at the clinic: 3.1 s in the browser
      test, 2.6 s on the dev database, 6.45 s for the independent agent clicking through the dashboard
- [x] An unpaired or revoked device cannot display a board: pairing screen for the page, `401` for state and
      stream; a removed box leaves its board by itself (tests)
- [x] A device silent for 10 minutes raises an alert naming the site: one message naming the screen and the
      clinic, sent to #14's team channel (test)
- [x] The box relaunches the board automatically after a power cut: in software, a browser reopened from its
      stored state goes straight to the board (test). The OS side (autostart, restore on AC power) is in
      Part A of the guide and **has not been tried on a real box**
- [x] Device status is visible in the platform-admin view: `/admin/display-devices` (test, screenshot)
- [ ] The provisioning guide has been followed successfully by someone who did not write it. **Not by a
      person.** An independent Claude agent, which had not seen the guide being written or the code, followed
      *Trying it without a box* and Part B and got a working board. Its nine findings are fixed here. A team
      member following Part A on a real box closes this.

## Risk and rollback

**Behaviour change: board URLs are no longer public.** A board opened by URL on a browser that is not paired
now shows a pairing code. Before release, every clinic's existing screens must be paired once (two minutes
each, Part B). No clinic uses boards in production yet (M8 is unreleased), so nothing live breaks today, but
this belongs in the v0.8.0 release note.

**Migration `0029`** creates `display_device` with three indexes. It is new and empty, so it is safe online.
The downgrade drops the table and unpairs every box.

**The watch** posts nothing unless `TEAM_WEBHOOK_URL` is set. Without it, alerts are only WARNING logs.

Rollback is a revert of this PR and `alembic downgrade 0028`.

**Known limits:**

- The removal delay is the stream's access check, 30 s. A removed box can show the numbers it already has
  for up to that long.
- A box whose browser profile is wiped (a re-imaged SD card) needs pairing again, and its old row stays until
  a manager removes it; it shows as *Not heard from* meanwhile.
- The heartbeat is per minute, so *Last heard from* is at most a minute old; silence is judged at 10 minutes.

Closes #61
