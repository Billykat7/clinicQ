# PR: The board says each call aloud: a chime, then the number and the room, one at a time, never a name (Issue 60 / M8-60)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#60](https://github.com/Billykat7/clinicQ/issues/60) · **Builds on:** #27 (display settings),
#57 (the live stream, PR #180) and #58 (the projection, PR #178), all merged; the spec's dependency list is
corrected in this PR · **Unblocks:** nothing waits on it

Patients look at their phones, and a number that only appears silently on a screen gets missed. With this
PR, a board plays a chime when a ticket is called and then says *"Number A 0 1 2, please go to Room 4"*
in the clinic's language:

- **Calls are said one at a time.** Two calls made together are said one after the other, never over
  each other.
- **The name is never said.** An announcement says the number and the room, and it is built so it cannot
  say anything else, whatever the screen shows. A browser test proves it with full names on the screen.
- **Muting keeps the highlight.** A clinic can mute its board or set how loud it is; a muted board still
  highlights every call.
- **A browser without speech** spells the number from recorded clips of each letter and digit.

**Not done here, and not claimed:**

- **No fluent speaker has checked any language**, English included. The five sentences are drafts
  written by Claude, an AI assistant.
- **No clips are recorded yet**, because nobody who speaks the languages has recorded them.
- **Nobody has listened to a real kiosk box.**

`docs/OPS/BOARD_AUDIO.md` holds the procedure and an empty record for each of these.

## Summary

- **What may be said** (`src/modules/display/announcements.py`, new):
  - one sentence per language, whose only blanks are `{number}` and `{room}`. `placeholders()` refuses
    any other blank, a format spec, or a missing or repeated blank;
  - English, Afrikaans, isiZulu, isiXhosa and Sesotho (the five the issue names), each with a BCP 47
    voice tag (`zu-ZA`). The other six board languages speak English, and the page is told so;
  - `checked_by` per sentence, `None` for all of them until a fluent speaker fills it in;
  - `number_clips()` finds recordings by file name under `src/static/audio/numbers/<language>/`.
- **The page** (`src/web/display.py`, `board.html`): the sentence, voice, English fallback, clips and
  chime go on the board as `data-announce-*` attributes, through the same `ensure_projected` check as
  everything else.
- **Who hears about a call** (`board.js`): when a call is first highlighted, board.js dispatches
  `board:call` with `{ number, room }` built from the ticket's number and its queue's room. Nothing else
  about the ticket goes into it.
- **Saying it** (`src/static/js/board-announce.js`, new):
  - a FIFO queue: chime, a short pause, the sentence, then a pause before the next call;
  - how a call is said, in order:
    1. speech with a voice for the language;
    2. recordings, when every character of the number has one;
    3. English speech;
    4. the chime alone;
  - every step has a time limit. A sentence the engine has not started within 2 s is cancelled, so an
    engine with no voice cannot hold on to a day of sentences (see *Found by the soak*);
  - mute and volume are read from the board payload at each call, so a dashboard change applies to the
    next call without a reload;
  - `window.ClinicQBoardAnnounce.log()` keeps the last 20 calls said.
- **Per-clinic volume** (migration `0030`, `site.announce_volume`, 0–100 with a check constraint, default
  80): in the display settings API, schema, audit and contract, and the projection's payload. Muting stays
  `announce_audio`.
- **The settings screen** (`settings_display.html`, `site-display-settings.js`, `admin.css`): the mute
  checkbox now says what is and is not said, and a *Loudness of announcements* slider, disabled while
  muted, is styled in the brand accent.
- **Sound without a click** (`src/core/security_headers.py`): `Permissions-Policy` is `autoplay=(self)` on
  `/display` and below, and stays `autoplay=()` everywhere else.
- **The chime** (`scripts/audio/make_chime.py` → `src/static/audio/chime.wav`, 55 KB): two bell-like notes
  (E5 then C5) made from sine waves, so there is no licence to track.
- **The spec's dependencies** (as asked): #60 now depends on **#27** (not #26, the typo) and **#58**, and
  the reverse "Unblocks" lists of #26, #27 and #58, the M8 milestone table, graph and "Needed from other
  milestones" list, and the issues README follow. The GitHub issue bodies are generated from these files by
  `scripts/gh_sync_docs.py`, which this PR does not run.

## Design notes

**Why the name cannot be said, rather than is not said.** The screen's payload may carry a name under
`name_lite` or `full`, so a voice that read "what the screen shows" would read it. Instead, the only
input to the speaking script is an event with two fields, filled from `ticket.number` and `queue.room`.
The only sentences it has are ones whose blanks are those two fields, and the server refuses any other.
Three tests hold this from three sides:

- the unit test refuses `{name}`, `{comment}`, attribute access and formatting;
- the integration test finds no part of a seeded name in anything the page is given to speak from, while
  the same page's payload does carry the name;
- the browser test sets full names with consent, checks the name is drawn on the screen, and finds no part
  of it in the spoken text, the events or the sounds played.

**Why recordings are a fallback and not shipped.** A browser voice for isiZulu, isiXhosa or Sesotho is
rare. On the one machine listed (a Mac's Chromium), only English voices exist. So recordings are the real
path for those languages. They must be made by a person who speaks the language. Machine speech from a
service or the OS whose terms do not allow shipping it in a product must not be used, and neither kind
was available. The mechanism is complete and tested with stand-in clips. The folder is empty, and a
language with no clips plays the chime and English, or the chime alone.

**Why the number is spelled.** *"A 0 1 2"* is read as four characters. *"A012"* can be read as "A twelve" or
as a word. Queue prefixes are letters and a room is typed by the clinic, so both are read as they are.

## Found by the soak

The 8-hour board soak from #56 runs the page in a headless Chromium with no voices. With the announcer
added, **JavaScript event listeners grew from 66 to 382** over 160 calls. The speech engine never started
the sentences and kept every one, with its handlers. The fix cancels a sentence not begun within two
seconds, clears its handlers, and cancels leftovers before the next. After it, listeners stayed at
**46 → 46**. The soak now also asserts that the announcer ran all day.

## Changes

- **New:**
  - `alembic/versions/0030_site_announce_volume.py`;
  - `src/modules/display/announcements.py`, `src/static/js/board-announce.js`, `src/static/audio/chime.wav`;
  - `scripts/audio/__init__.py`, `scripts/audio/make_chime.py`;
  - `docs/OPS/BOARD_AUDIO.md`;
  - `tests/unit/display/test_announcements.py`, `tests/integration/display/test_board_announcements.py`,
    `tests/e2e/display/test_board_announce.py`;
  - `docs/GITHUB/PR/M8/assets/pr60/*.png`.
- **Settings:** `SITE_DEFAULT_ANNOUNCE_VOLUME`; `Site.announce_volume` and its check constraint;
  `DisplaySettingsIn/Out`, `DisplaySettingsChange` and its audit, the router, `contracts/sites.yaml`.
- **Board:** `projection.py` (`announce_volume` in `BoardState` and the payload), `board.js` (`board:call`),
  `board.html`, `src/web/display.py`.
- **Security headers:** `permissions_policy_for(path)`.
- **Tests updated:**
  - the security-header, display-settings API and board-privacy unit tests;
  - the e2e board fixture resets the clinic's display and audio settings before each test;
  - the 8-hour soak checks the announcer ran.
- **Docs:**
  - the spec's dependencies (`ISSUE_60`, `ISSUE_26`, `ISSUE_27`, `ISSUE_58`, `docs/GITHUB/ISSUES/README.md`, M8);
  - `docs/PRODUCT/04-display-monitor.md` (*Announcements*), `docs/OPS/KIOSK_SETUP.md` (troubleshooting);
  - the M8 status and the chime exit criterion, marked partly met;
  - progress: `docs/GITHUB/README.md`, `README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (274 files)
- [x] `make milestone-progress-check ARGS='--assume-closed 60'`: 14 milestones up to date
- [x] Whole suite as CI runs it, in UTC with PostgreSQL, Redis and Chromium:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest -q --no-cov -n auto --dist loadscope
  2062 passed, 1 skipped, 9 xfailed in 226.84s
  ```

- [x] The new tests (30 with their parameters):

  ```text
  tests/unit/display/test_announcements.py::test_every_sentence_says_the_number_and_the_room_and_nothing_else
  tests/unit/display/test_announcements.py::test_a_sentence_with_any_other_blank_is_refused
  tests/unit/display/test_announcements.py::test_no_personal_field_name_can_be_a_blank
  tests/unit/display/test_announcements.py::test_each_language_the_issue_names_has_its_own_sentence_and_others_speak_english
  tests/unit/display/test_announcements.py::test_no_sentence_claims_a_fluent_speakers_check_it_has_not_had
  tests/unit/display/test_announcements.py::test_recordings_are_found_by_character_and_nothing_else_counts
  tests/unit/display/test_announcements.py::test_the_committed_chime_is_the_one_the_script_makes
  tests/integration/display/test_board_announcements.py::test_the_page_speaks_the_clinics_sentence_in_its_language_and_english_where_there_is_none
  tests/integration/display/test_board_announcements.py::test_nothing_the_page_speaks_from_holds_a_name_even_when_the_screen_shows_one
  tests/integration/display/test_board_announcements.py::test_the_clinics_mute_and_volume_reach_every_board_response
  tests/integration/display/test_board_announcements.py::test_the_chime_is_served_to_the_board
  tests/integration/sites/test_display_settings_api.py::test_a_manager_sets_how_loud_announcements_are_within_0_to_100_and_it_is_audited
  tests/integration/platform/test_security_headers.py::test_only_the_waiting_room_board_may_autoplay_its_own_sound
  tests/e2e/display/test_board_announce.py::test_a_call_plays_the_chime_then_says_the_number_and_the_room
  tests/e2e/display/test_board_announce.py::test_two_calls_at_the_same_moment_are_said_one_after_the_other
  tests/e2e/display/test_board_announce.py::test_no_name_is_ever_said_even_with_full_names_on_the_screen
  tests/e2e/display/test_board_announce.py::test_a_muted_clinic_still_highlights_the_call_and_a_volume_is_the_volume_heard
  tests/e2e/display/test_board_announce.py::test_a_browser_without_speech_spells_the_number_from_recorded_clips
  tests/e2e/display/test_board_announce.py::test_an_isizulu_board_speaks_isizulu_and_falls_back_to_english_without_a_voice
  tests/e2e/display/test_board_announce.py::test_the_board_may_play_sound_without_a_click_and_the_dashboard_may_not
  30 passed in 27.70s
  ```

  The browser tests replace the page's speech engine with a stand-in that takes 400 ms per sentence and
  records what it was asked. The chime and clips are real `<audio>` in Chromium. What they printed:

  ```text
  said: 'Number T 0 0 1, please go to Room 2.', 1818 ms after the chime began
  'Number T 0 0 1, please go to Room 2.': 1687–2088 ms
  'Number A 0 0 1, please go to Room 4.': 4340–4742 ms
  ```

- [x] **On the dev database, with the real speech engine.** A kiosk box was paired with Hillbrow through the
  API, in headless Chromium on a Mac. Two calls were made from a second process (through Redis), and the
  board used the Mac's own voices; nothing was replaced:

  ```text
  voices in this Chromium: 180
  autoplay allowed: True
  called T002 A002
  {'number': 'T002', 'room': 'Room 2', 'how': 'speech', 'text': 'Number T 0 0 2, please go to Room 2.'} 6.6s
  {'number': 'A002', 'room': 'Room 4', 'how': 'speech', 'text': 'Number A 0 0 2, please go to Room 4.'} 5.7s
  second began after first ended: True
  ```

  Headless, so nobody heard it. The durations are the engine's own start and end events. The Mac's voices
  for the five languages: `en-ZA` and five other English voices; **none** for af, zu, xh or st.

- [x] **The settings screen** on the dev database: the volume saved as 60 and was read back from the API. Unticking
  *Announce* disables the slider. The served policies are `autoplay=(self)` on `/display` and `autoplay=()`
  on `/dashboard`.
- [x] **The 8-hour soak** (#56's, with the announcer): heap 2.05 → 2.14 MB, nodes 600 → 600, listeners
  46 → 46, layout unchanged.
- [ ] **A fluent speaker's check of each language:** not done (below).
- [ ] **A real kiosk box heard in a room:** not done.

### Screenshots

The announcement settings, with the new loudness slider:

![Settings](https://github.com/Billykat7/clinicQ/blob/658c895887619df4a759fc60ea1fa6d501f1d965/docs/GITHUB/PR/M8/assets/pr60/settings-announcements-1366.png?raw=true)

Muted: the slider is disabled, and the words say the screen still highlights each call:

![Muted](https://github.com/Billykat7/clinicQ/blob/658c895887619df4a759fc60ea1fa6d501f1d965/docs/GITHUB/PR/M8/assets/pr60/settings-announcements-muted-1366.png?raw=true)

At phone width (390 px):

![Phone](https://github.com/Billykat7/clinicQ/blob/658c895887619df4a759fc60ea1fa6d501f1d965/docs/GITHUB/PR/M8/assets/pr60/settings-announcements-390.png?raw=true)

The board looks the same as before; it only gains sound.

## Acceptance criteria

- [x] A called ticket produces a chime followed by a spoken number and room: chime first, the sentence
      after the chime ends (browser test); real speech engine on the dev database (above)
- [x] Two simultaneous calls are announced sequentially, not overlapping (browser test; dev database)
- [ ] The announcement is comprehensible in each supported language, verified by a native or fluent
      speaker on the team. **Not done.** No fluent speaker was available. The sentences are drafts written
      by Claude, an AI assistant, and `checked_by` is empty for every language (a unit test keeps it that
      way until someone signs). `docs/OPS/BOARD_AUDIO.md` has the procedure and the table to record it.
      The spec asks the checker to be named in this PR; there is nobody to name.
- [x] Announcements never include a patient name, proven by a test: unit, integration and browser tests,
      the last with full names on the screen
- [x] Audio can be muted per site without disabling the visual highlight (browser test; settings screen)
- [x] A browser without speech support falls back to pre-rendered number audio: the mechanism, with speech
      removed from the page and stand-in clips (browser test). **No real recordings exist yet**, so in
      production today such a browser plays the chime alone.

## Risk and rollback

**Migration `0030`** adds one non-null column with a server default and a check constraint; existing
clinics get 80. It is reversible: the downgrade drops both and loses only each clinic's volume.

**Sound in waiting rooms.** Boards now make sound by default (`announce_audio` has defaulted to on since
#27). A clinic that does not want it unticks it. A TV's own volume still applies.

**Permissions-Policy** is loosened for `/display` only, and only for `autoplay`, only for this origin.

Rollback is a revert of this PR and `alembic downgrade 0029`.

**Known limits:**

- Languages without a voice on the box, and without recordings, speak English. On the Mac checked, that
  is every language except English.
- The room is read as the clinic typed it: *Room 4* inside an isiZulu sentence is read with English words.
- A browser that lists no voices but has a default one is tried in the clinic's language before English.
- The e2e test browser runs with autoplay allowed, so the page-level policy is checked through
  `document.featurePolicy`, not by a refused `play()`.

Closes #60
