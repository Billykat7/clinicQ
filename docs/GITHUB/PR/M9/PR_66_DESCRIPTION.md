# PR: Patient messages become versioned, per-language templates with an editor that refuses a broken one (Issue 66 / M9-66)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#66](https://github.com/Billykat7/clinicQ/issues/66) · **Builds on:** #63, #64 and #65 (PRs #186,
#188 and #189), all merged · **Depends on, still to come:** #77 (M10, the translations) · **Unblocks:** #71

A "you are next" message in a language the patient does not read is a message that did not arrive. With this
PR:

- **Every patient message is a template, per channel and language.** Its words live in
  `src/locales/<language>/notifications.toml`. It is the same registry `templates.py` already was, now with
  language and version added, not a second one.
- **Every message sent names the exact version it used**, and versions are never changed or deleted. Any
  message can be reproduced later, and a retry says what the first attempt said, even if the template was
  edited in between.
- **A broken template fails when it is registered, never when it is sent.** That covers an unknown or unsafe
  blank, no ticket number, a web push saying more than the number and clinic, and an SMS longer than one
  segment.
- **Operators have a console and an editor.** The editor shows exactly what the patient receives, per
  channel, with an SMS character and segment counter.

**The order of work, and who agreed it.** #77 (translations) is scheduled in sprint 8 of M10, and this issue
in sprint 4, so the issue cannot be finished in the order its dependencies suggest. The brief for this work
set the workable order: the registry, versioning, the editor and the English templates now, and isiZulu,
isiXhosa, Afrikaans and Sesotho when #77 lands. **That order has not been confirmed with F (Data & Research
Lead) as the brief asks.** I could not reach F, and the confirmation is not recorded anywhere. It is this PR's
assumption, and F should confirm or change it before #77 starts.

**Not done here, and not claimed:**

- **Only English is written.** The other four languages have no words yet, so a patient who prefers one of
  them gets English, and the ledger says `en`.
- **No fluent speaker has checked anything.** The English file credits itself, "Written in English by the team;
  no separate review".
- **The console has no menu entry.** It is reached by typing `/admin/notification-templates`; nothing else
  links to it yet.

## Summary

- **The registry** (`src/modules/notifications/template_registry.py`, new), keyed by
  `(template, channel, language)` for the six ticket messages (next, called, recalled, no-show, transferred,
  cancelled) in SMS, WhatsApp and web push:
  - `builtin(language)` loads and validates a locale file: that is the registration step, and it raises
    naming the file, template and channel;
  - `current(db, …)` returns the newest version, storing a file version the first time it is used;
  - `publish(db, …)` stores an edited version numbered above everything before it;
  - `render_version(row, context)` renders a stored version.
- **The variable contract** (`validate`):
  - blanks must be plain names from the message's list (`VARIABLES`: `app`, `number`, `clinic`, `queue`,
    `room`, `where`, plus `minutes` for a recall and `wait` for a transfer). Attribute, index, conversion and
    format-spec blanks (`{number.__class__}`) are refused, because an edited template must not reach inside a
    value;
  - `{number}` is required;
  - web push may use only `{number}` and `{clinic}` (#64's lock-screen rule, now enforced for edited words too);
  - web push needs a title; SMS and WhatsApp have none;
  - an SMS must fit one GSM 7-bit segment with the longest names the database allows (#65).
- **Locale files:**
  - `src/locales/en/notifications.toml` holds the #63 to #65 words, each `version = 1`;
  - `src/locales/README.md` says how to add a language;
  - `src/locales/notifications.lock.json` (from `python -m scripts.lock_notification_templates`) holds each
    version's hash, and a test fails when words change without a new version.
- **The ledger** (migration `0035`):
  - `notification_template_version` rows are immutable, unique by template, channel, language and version,
    and credit `created_by` and `reviewed_by`;
  - `notification.template_version_id` (`RESTRICT`: a sent version cannot be removed) and
    `notification.language`;
  - `patient_notification_preference.language`.
  - The foreign key is named by hand: the naming convention's name would be 66 characters, past PostgreSQL's
    63.
- **The service** (`service.py`):
  - `notify` and the fallback pin a version on the row: `language_for` uses the patient's language, else the
    clinic's board language if it is one of the five, else English, and falls back to English when the
    language has no words for that message;
  - `attempt` renders the pinned version (`_message_for`);
  - rows from before the migration render the current English words;
  - `templates.render` asks the registry for these six templates, and the hard-coded ticket renderers and
    push words are gone.
- **The editor API** (`template_router.py`, `logs` read, and `logs` update to publish):
  - `GET /notifications/templates` lists them;
  - `GET /notifications/templates/{template}/{channel}/{language}` returns one with its blanks and history;
  - `POST /notifications/templates/preview` returns exactly what a patient receives, from a sample ticket,
    with the SMS count and the worst-case count, or `valid: false` and the reason;
  - `POST /notifications/templates/{…}/versions` publishes, or answers `422` with the reason;
  - `GET /notifications/templates/versions/{id}` returns one stored version.
  - The router is registered before `GET /notifications/{notification_id}`, so "templates" is never read as an
    id. `NotificationRead` gains `template_version_id` and `language`.
- **The pages** (`src/web/routes.py`):
  - `/admin/notification-templates/{sms|web-push|whatsapp}` (`notification_templates.html`,
    `admin-notification-templates.js`) follows the list-view rules: a tab per channel, a filter bar, sortable
    columns and a row quick view whose full view is the editor;
  - `/admin/notification-templates/{tab}/{template}/{language}` (`template_editor.html`,
    `admin-template-editor.js`) has the words, a debounced live preview in a message bubble, the counter, a
    publish button gated on `logs` update, and the version history.

## Design notes

**Why a stored row per version, not just a version number.** "Reproduce exactly" needs the words, not a
number, and the locale file will change. So the first time a file version is used it is written to the table
and never changed. Edits add rows above it. The lock test is what keeps "version 1" meaning one set of words:
change the words, and the version number has to rise.

**Validation happens where a template enters the registry.** Locale files are validated when loaded, which
every notification test does, so a broken file fails CI. Edits are validated in `publish`, so the editor
refuses them with a sentence. Sending never validates, because by then the words are known to be good. The
contract is written once and used by both paths, and by the preview.

**Web push's rule moves into the contract.** #64 kept payloads to the number and clinic by hand-writing those
words. Now that operators can edit templates, the rule has to hold for any words, so `PUSH_VARIABLES` is part
of validation, and `test_push_payload.py` still renders every push from a context full of forbidden values.

**Language falls back quietly, and the ledger says which language was used.** A patient who asked for
isiZulu is not left without a message because the isiZulu words are not written yet: they get English, and
their ledger row says `en`. When #77 adds `src/locales/zu/notifications.toml`, the same code sends isiZulu with
nothing else changed. The integration test proves this with test-only isiZulu words in a temporary locale
folder.

## Changes

- **New:**
  - `src/modules/notifications/template_registry.py`, `template_router.py`
  - `src/database/models/notification_template.py`, `alembic/versions/0035_notification_template_versions.py`
  - `src/locales/README.md`, `src/locales/en/notifications.toml`, `src/locales/notifications.lock.json`
  - `scripts/lock_notification_templates.py`
  - `src/templates/admin/notification_templates.html`, `template_editor.html`
  - `src/static/js/admin-notification-templates.js`, `admin-template-editor.js`
- **Changed:**
  - `templates.py` (ticket words moved to the locale file), `service.py`, `schemas.py`, `enums.py`
    (`NOTIFICATION_LANGUAGES`, `DEFAULT_NOTIFICATION_LANGUAGE`, `TemplateSource`)
  - `notification.py`, `patient_notification_preference.py`, `models/__init__.py`, `api/v1/router.py`,
    `web/routes.py`, `admin.css` (the preview bubble)
- **Tests, new:**
  - `tests/unit/notifications/test_template_registry.py` (18)
  - `tests/integration/notifications/test_template_versions.py` (3)
- **Tests, updated:** `tests/unit/notifications/test_sms_segments.py`, which now renders every SMS from the
  registry in every written language.
- **Docs:** the Issue 66 spec (files), the M9 status row and progress bars (`--assume-closed 66`), and the
  README Status block (67 of 109).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (297 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2195 passed,
  1 failed**. The failure was M8's
  `test_board_resilience.py::test_a_day_offline_leaves_the_heap_the_listeners_and_the_layout_where_they_were`:
  the board's listener count read 39 then 45 while every CPU was busy. It passed when run again on its own
  (`1 passed in 93.94s`). No board code is touched here.
- [x] **How to verify, steps 1 and 3**, run on PostgreSQL:

  ```text
  STEP 1: register a template missing a variable
    refused at registration: {minuts} cannot be used in this message: it is not in the message's variables.

  STEP 3: send a message, edit the template, then look the send up
    sent: ledger row 7ddd2d2b… status=failed (attempt 1 failed) template_version_id=01a0a2ed… = ticket_called/sms/en v1
    edited: published ticket_called/sms/en v2 by ops@clinicq.example
    looked up: row status=sent, version v1: '{app}: ticket {number}, please come in now to {where} at {clinic}.'
    the retry sent: 'BK ClinicQ: ticket A043, please come in now to Room 2 at Glen Earle Clinic.'
    reproduced from the stored version and context: True
  ```

- [x] **How to verify, step 2: the preview per channel**, in the editor on the development server as an
  operator:
  - the SMS recall shows *130 characters, 1 segment (gsm7); with the longest names: 156 characters, 1 segment*;
  - typing `{minuts}` shows the refusal and disables publishing;
  - web push shows its title and the sample ticket's body.

  The preview is the same renderer the service uses (`template_registry.render`), which
  `test_template_versions.py` checks over HTTP, including an en dash sent and counted as a hyphen.
- [x] Unit (`test_template_registry.py`, 18 passed):
  - 13 ways a version is refused at registration, each with its sentence;
  - a broken locale file does not load, naming `[ticket_next.sms] (en)`;
  - every event has words in every channel in every written language;
  - the lock test;
  - the same version and context render identical words.
- [x] Integration (`test_template_versions.py`, 3 passed):
  - the ledger names the version, the retry after an edit repeats version 1 while the next message uses
    version 2, and `GET /notifications/{id}` → `GET …/versions/{id}` returns the words;
  - publishing `{minuts}` or a web push naming `{room}` is 422, and a receptionist gets 403 on the list;
  - a patient preferring isiZulu gets the (test-only) isiZulu words with `language = zu`, and one preferring
    isiXhosa, which has no words, gets English with `language = en`.

| Console, SMS | Row quick view | Editor, SMS counter |
|---|---|---|
| ![The SMS templates console with the filter bar and sortable table](https://github.com/Billykat7/clinicQ/blob/6d88e412faaf3e3c75a1eb2ad4a823c16bc6bc4c/docs/GITHUB/PR/M9/assets/pr66/console-sms.png?raw=true) | ![A template's words in the slideover beside the list](https://github.com/Billykat7/clinicQ/blob/6d88e412faaf3e3c75a1eb2ad4a823c16bc6bc4c/docs/GITHUB/PR/M9/assets/pr66/console-quick-view.png?raw=true) | ![The recall SMS in the editor with its live preview and segment count](https://github.com/Billykat7/clinicQ/blob/6d88e412faaf3e3c75a1eb2ad4a823c16bc6bc4c/docs/GITHUB/PR/M9/assets/pr66/editor-sms.png?raw=true) |

| A misspelt blank, refused before publishing | Web push: title and body |
|---|---|
| ![The editor refusing {minuts} with publishing disabled](https://github.com/Billykat7/clinicQ/blob/6d88e412faaf3e3c75a1eb2ad4a823c16bc6bc4c/docs/GITHUB/PR/M9/assets/pr66/editor-refused.png?raw=true) | ![The web push editor previewing the title and the body](https://github.com/Billykat7/clinicQ/blob/6d88e412faaf3e3c75a1eb2ad4a823c16bc6bc4c/docs/GITHUB/PR/M9/assets/pr66/editor-web-push.png?raw=true) |

## Acceptance criteria

- [ ] **Every event has a template in all five languages:** English only. Every event has English words in
  all three channels, and a test requires the same of every language that gets a file. The other four wait
  for #77, in the order recorded above.
- [ ] **SMS variants fit in one segment in every language:** English fits (the worst case is 156 characters,
  GSM 7-bit), and registration refuses any SMS that does not, so the other four languages will be held to it
  when they arrive. Not ticked, because four of the five languages do not exist yet.
- [x] **A missing variable fails at template-registration time, not at send time:** 13 refusal cases, a
  broken locale file that does not load, and the editor's 422 (steps 1 and 2).
- [x] **The preview shows exactly what the patient will receive, per channel:** the preview renders with the
  service's renderer, and for SMS the GSM normalisation, segment count and worst case; screenshots per channel.
- [x] **The version used for a send is recorded in the notification log:** `template_version_id` and
  `language` on every patient message, and a retry after an edit still sends and reports version 1 (step 3).
- [ ] **Translations have been checked by a fluent speaker and the reviewer is credited:** no translation
  exists and nobody has reviewed anything. `reviewed_by` is there to credit the reviewer, and the editor has
  a field for it.

## Risk and rollback

- **Words unchanged.** The English messages are exactly #63 to #65's, now in a file. No patient sees a
  difference on merge.
- **A version is stored the first time it is used**, in a savepoint that tolerates two workers storing the same
  version at once.
- **Operators can now change what patients read.** The contract refuses unsafe or broken words, every change is
  a new version naming its author, and a bad version is replaced by publishing a better one.
- **Migration `0035`** adds one table and three nullable columns. The previous release ignores them.
- **Rollback** is a revert and a downgrade to `0034`. Edited versions are lost, and messages go out in the
  locale file's words as before.

Closes #66
