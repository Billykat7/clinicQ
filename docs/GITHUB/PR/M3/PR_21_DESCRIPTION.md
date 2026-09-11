# PR: Consent, defaulting to no, withdrawable at once (Issue 21 / M3-21)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#21](https://github.com/Billykat7/clinicQ/issues/21) · **Builds on:** #15–#20 (PRs #129–#134)

> **Merge order:** after #129–#134; the Conventions check fails on their commits until then.

Nothing for patient consent existed. The kernel had the one thing that makes this issue small: every
notification already resolves through `preferences.resolve`, so there is a single place to ask. The
board has no renderer yet (Issue 58), so this PR defines the *only* shape a screen may be given and
locks the door before the renderer arrives.

## Summary

- **Two tables, one answer.** `patient_consent` holds the current answer per patient per purpose —
  one row, unique on `(patient_id, purpose)`. `patient_consent_event` keeps every answer ever given,
  with the channel, the wording version, the clinic and the staff member who recorded it at the desk.
  The current row is what the surfaces read; the history is the proof.
- **No row means no.** `has_consent()` returns `False` for withdrawn, for refused **and for never
  asked**, so the most private option is the default on the web, on USSD, on WhatsApp and at the
  desk without anyone setting it. A patient who has never answered has no consent row at all.
- **One door per direction.**
  - *Messages:* `preferences.resolve` asks `has_consent()` **first**, and `service.attempt`
    re-resolves at delivery, so a withdrawal stops a message that was already queued.
  - *Screens:* `consent.board_projection` is the only function that turns a patient into something a
    public screen may show; it applies the site's display mode **and** the consent together.
- **A sign-in code is never gated.** It is the service the patient asked for by typing their number
  (POPIA s11(1)(b)), not a message they would have to consent to before they could sign in to
  consent. That exemption is one named frozenset, not a special case scattered through the code.
- **The patient's own page** (`/me/consent`) and `GET`/`PUT /api/v1/patients/me/consents`, with the
  question text served from the same constants that are recorded with the answer.
- **A guard test** that fails the build if a send path stops resolving, if a transport is called from
  outside the notification service, or if anything outside `src/modules/patients` reads
  `Patient.display_name`.

## Design notes

**Consent is not a preference, and they are not merged.** A preference is "I would rather not be
emailed about this"; consent is "you may do this to me at all". They are stored apart, and `resolve`
asks consent *before* preferences, so an unsubscribe token can never be read as permission and a
missing preference can never be read as consent. `_patient_consent_denies` matches a patient by the
recipient number or address, so the check works on the existing send paths without every caller
learning about patients.

**Withdrawal is an ordinary answer, and "immediately" is tested at the hardest point.** Both surfaces
read the current row at render and at delivery — not at enqueue — so the interesting case is a
message accepted while consent held and delivered after it was withdrawn. That is a test, and the
ledger row says `suppressed by preference (no-patient-consent)` rather than silently vanishing.

**The board's contract is `BoardEntry`, not `Patient`.** A template that is handed a patient will
eventually show a name. `board_projection` returns a frozen dataclass of ticket number, name and
comment, with name and comment `None` unless both gates allow them, so a name under `NUMBER_ONLY`
is *not in the response object* and cannot leak through a template or be un-hidden with CSS. The
guard test enforces that no module outside `patients` reads `display_name` at all. Issue 58 renders
this; Issue 27 sets the mode.

**The comment is its own question.** "Show my name" and "show why I am here" are not one decision:
the second is health information on a public screen, so it is a separate purpose, and it is refused
under any display mode but `FULL` even when granted.

**An answer is recorded even when it repeats the current one** — "asked again on another channel and
said the same" is itself a fact worth keeping, and it is what makes the history a defensible record.

**The audit row says what changed, not who the patient is:** `notifications=withdrawn via web`, with
no name, number or wording text (Issue 20's minimisation rule).

**Wording, and who owns it.** The text is committed in code (`consent_text.py`) so the web page, the
USSD menu and the desk ask the same question, and so the version shown is stored with the answer.
It is written to POPIA s18's plain-language expectation — what happens, who does it, that it is
optional, that it can be changed — with a separate short line per question for a USSD screen. It is
**F's draft**, versioned `2026-09-v1-draft` and named as a draft in the module docstring: see the
criterion below.

**Out of scope:** the USSD/WhatsApp consent menu path (Issue 73 owns the menu; the service call and
the shorter wording it needs are here and used by the gateway-created patient path), the board
renderer (Issue 58), retention of consent history (Issue 95).

## Changes

- **`alembic/versions/0005_patient_consent.py`** (new): `patient_consent` and
  `patient_consent_event`, both `ON DELETE CASCADE` from `patient`, unique
  `(patient_id, purpose)` on the current table, indexes for the history read.
- **`src/database/models/patient_consent.py`** (new), `src/commons/enums.py`: `ConsentPurpose`
  (four purposes) and `AuditEntityType.PATIENT_CONSENT`.
- **`src/modules/patients/consent.py`** (new): `has_consent`, `consent_state`, `record_consent`,
  `consent_required_for`, `BoardEntry`, `board_projection`.
- **`src/modules/patients/consent_text.py`** (new): the wording, the USSD wording, the intro, the
  withdrawal notice and `CONSENT_WORDING_VERSION`.
- **`src/modules/notifications/preferences.py`:** `_patient_consent_denies`, called first in
  `resolve`; **`service.py`:** `attempt` re-resolves before it delivers.
- **`src/modules/patients/router.py` / `schemas.py`:** `GET` and `PUT /patients/me/consents`; the
  session is the identity, so the route takes no patient id.
- **`src/web/routes.py`, `src/templates/patient/consent.html`, `src/static/js/patient-consent.js`,
  `src/static/css/layouts.css`:** the patient's page, rendered from the session cookie with no
  database read, signed-out shell included.
- **`tests/`:** `integration/patients/test_consent.py` (new, 11 cases),
  `unit/security/test_consent_is_not_bypassable.py` (new, 6 cases incl. 2 fixtures proving the
  guard fails), plus the consent route in the cross-tenant, unaudited-mutation and surface-gate
  guards.

## Testing

- [x] `ruff check`, `ruff format --check`, `mypy src/` (183 files) clean.
- [x] `make test`: **1162 passed**, 20 skipped, 9 xfailed. PostgreSQL and Redis: **20 passed**
      (migration `0005` round-trips and autogenerate finds no drift).
- [x] **How to verify, on a real server** (PostgreSQL migrated to `0005`, a patient signed in with
      an OTP; the issue's own three steps):

```text
1. Sign in and touch nothing          GET /api/v1/patients/me/consents
   wording_version 2026-09-v1-draft
     display_name     granted=False        ← and no row written at all
     display_comment  granted=False
     notifications    granted=False
     feedback_survey  granted=False

2. Grant, then withdraw               PUT /api/v1/patients/me/consents/notifications
     grant    -> notifications: True
     withdraw -> notifications: False
     notice: "Done. This takes effect straight away: the screen and our messages follow your
              new answer from now on. We keep a record that you changed it, and when, and
              nothing else."
   PUT /consents/whatever_we_like -> 422     GET /me/consents with no session -> 401

3. The same message to two patients   (one said yes, one never answered)
     said yes   +27834445566  -> status=sent       reason=-
     never said +27798887766  -> status=suppressed reason=suppressed by preference (no-patient-consent)

$ psql:
  patient  |    purpose    | granted | source_channel | wording_version
  01a0929f | notifications | t       | web            | 2026-09-v1-draft   ← history keeps
  01a0929f | display_name  | t       | web            | 2026-09-v1-draft     every answer,
  01a092a3 | notifications | t       | web            | 2026-09-v1-draft     including the
  01a092a3 | notifications | f       | web            | 2026-09-v1-draft     one withdrawn

  patient_consent (current state, one row per purpose):
  01a0929f | display_name  | t
  01a0929f | notifications | t
  01a092a3 | notifications | f

  audit_event: update | patient_consent | patient:01a092a3-… | notifications=withdrawn via web
                                                               ↑ what changed, no personal data
```

- [x] **Screenshot** — `/me/consent`, signed in, with two answers granted. The files are in
      `docs/GITHUB/PR/M3/assets/pr21/`.

| Light | Dark |
|---|---|
| ![The patient consent page, light](https://github.com/Billykat7/clinicQ/blob/24f65ea1ebf7961a528bdc581b7d4375742cf91f/docs/GITHUB/PR/M3/assets/pr21/consent-light.png?raw=true) | ![The patient consent page, dark](https://github.com/Billykat7/clinicQ/blob/24f65ea1ebf7961a528bdc581b7d4375742cf91f/docs/GITHUB/PR/M3/assets/pr21/consent-dark.png?raw=true) |

## Acceptance criteria

- [x] **Consent defaults to the most private option on every channel.** No row means no, so the
      default is structural rather than a value someone has to remember to write. Tested for a web
      sign-in and for patients created by the USSD and WhatsApp gateway paths.
- [x] **Withdrawing display consent removes the name from the board on the next render.**
      `board_projection` reads the current answer at render time; tested granted → shown,
      withdrawn → `name=None, comment=None`, number still there.
- [x] **Withdrawing notification consent stops sends immediately, proven by a test.** Two tests: a
      send with no consent is suppressed with the reason on the ledger row, and a message **queued
      while consent held** is suppressed at delivery after it is withdrawn.
- [x] **Consent capture records the channel it came from.** `source_channel` on both tables; tested
      across web and a desk-recorded answer, which also stores the staff member and the clinic.
- [x] **The board and notification services cannot bypass `has_consent()`, enforced by a guard
      test.** `test_consent_is_not_bypassable.py` asserts `resolve` still asks, every send path
      still resolves, no transport is called from outside the notification service, and nothing
      outside `src/modules/patients` reads `display_name`. Two fixtures prove the guard fails on
      the shapes it exists to catch.
- [ ] **Consent wording is reviewed against POPIA plain-language expectations and committed.**
      *Committed, not yet reviewed.* The wording is written to s18's expectation and versioned
      `2026-09-v1-draft`, and the module says in its docstring that it is F's draft awaiting their
      review. The review is F's call, not mine, and marking this criterion done before they have
      read it would be the kind of claim this trail exists to prevent. Bumping the version after
      review costs one constant; every answer already stores which version it was given under.

## Risk and rollback

**Migration `0005`** adds two new tables and touches nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible. Behaviour change, and it is the point
of the issue: **from this deploy, a message to a patient who has not consented is suppressed rather
than sent.** There is no patient consent data today, so on deploy every patient's answer is "no"
until they are asked — for queue notifications that is the correct starting state under POPIA, and
Issue 73's menu plus this page are how a patient says yes. Sign-in codes are unaffected.

**Follow-ups noticed:** the USSD menu that asks these questions on a feature phone is Issue 73 (the
wording and the service call are ready for it); the board that reads `board_projection` is Issue 58;
consent history retention is Issue 95. `_patient_consent_denies` matches a patient by recipient
address, which is exact for SMS and WhatsApp; when patients gain email addresses it should match on
the patient id the send already knows.

Closes #21

🤖 Generated with [Claude Code](https://claude.com/claude-code)
