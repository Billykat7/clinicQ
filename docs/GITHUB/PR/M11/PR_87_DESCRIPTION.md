# PR: One question after each visit, answered with one tap or one digit, and a clinic's report (Issue 87 / M11-87)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#87](https://github.com/Billykat7/clinicQ/issues/87) · **Builds on:** #6 (redaction), #21 (consent), #41 (the
lifecycle), #63, #65 and #67 (notifications, SMS replies, preferences), all merged

This is modelled on the NHS Friends and Family Test: one question after the visit, one tap to answer, and a
number a clinic manager can take to their district. With this PR:

- **Every completed visit asks once, "How was your visit today?"**, and never asks again. A transfer's earlier
  legs ask nothing, because the visit ends once.
- **Consent comes first.** The request goes only to a patient who agreed to "one short message after my
  visit". The join page now asks that beside the messages question. An opt-out (SMS STOP) or a muted message
  also stops it. Those requests are recorded as suppressed and left out of the response rate.
- **Answering is one action on each channel:**
  - SMS: reply with a digit from 1 to 5 (anything after it is the comment);
  - web push: opens the answer page, where one tap on a score answers;
  - the patient's own ticket page: shows the question once the visit is done, answered with one tap.
- **A comment is screened before it is stored.** Phone numbers, e-mail addresses, ID numbers and codes are
  replaced by `[REDACTED:…]`, so no report can show them. The comment is emptied after the clinic's
  patient-text retention window; the score is kept.
- **The clinic can see whether the question works:** requests sent, answered, the response rate and the
  average score, per clinic, queue and the staff member who marked the visit done, plus answers per channel
  and recent screened comments.

**Not done here, and not claimed:**

- **F's sign-off on the question, the 1–5 scale and the report is not recorded, and I could not obtain it.** The
  issue gives F the survey design and reporting, with C building the UI in sprint 12. The wording follows the
  consent text the team already wrote for `feedback_survey`. F should confirm or change it before sprint 12.
  The words live in one place (`FEEDBACK_QUESTION`, `SCORE_LABELS`, the message template), so changing them is
  one edit plus a new template version.
- **WhatsApp:** the message is written for WhatsApp and says to reply with a digit, but no inbound WhatsApp
  adapter exists yet (Issue 75, M10). `feedback.answer_by_reply(..., channel=FeedbackChannel.WHATSAPP)` is
  the function it should call.
- **"Scores aggregate per site and per queue in the M12 reports":** the aggregation and its API are here. The M12
  report screens (Issues 89 and 94) do not exist yet.

## Summary

- **The model** (`src/database/models/feedback.py`, migration `0041`), `visit_feedback` with:
  - `uq_visit_feedback_visit` and an unguessable `token`;
  - `request_status` (`sent` or `suppressed`) and `served_by` (who marked the visit done);
  - `score` (`ck_visit_feedback_score_range`, 1–5), `comment` (screened), `comment_redactions`,
    `comment_expires_at`, `answered_at`, `answered_via` and `expires_at` (7 days).
- **The service** (`src/modules/appointments/feedback.py`):
  - `request_after_visit` is called from `transition_ticket` on `done`;
  - `answerable` and `answer` accept one answer and refuse the rest: `feedback.already_answered` and
    `feedback.expired` (409), `feedback.not_found` (404);
  - `answer_by_reply` takes a reply beginning with a digit from 1 to 5;
  - `purge_expired_comments` empties comments past retention;
  - `feedback_report` builds the report.
- **Screening** (`src/core/log_redaction.py`): `screen_free_text` applies Issue 6's patterns (phone numbers,
  codes, tokens) plus e-mail addresses and South African ID numbers, and returns the screened text and a count.
- **Consent** (`src/modules/patients/consent.py`): `consent_required_for(TICKET_FEEDBACK)` is
  `FEEDBACK_SURVEY`, so the notification service's existing gate decides.
- **The message**: `PatientEvent.FEEDBACK` and `ticket_feedback` on SMS, WhatsApp and web push, one GSM-7
  segment with worst-case names. It is not urgent, so quiet hours hold it. It can be muted ("After my visit, to
  ask how it went"). A web push opens `/f/{token}`.
- **SMS replies** (`src/core/webhook_gateways/africastalking.py`): a reply that is not STOP or START is offered
  to `answer_by_reply`. The outcome `rated` joins `stopped`, `restarted` and `ignored`.
- **The API** (`feedback_router.py`, `contracts/feedback.yaml`):
  - `GET` and `POST /api/v1/feedback/{token}` (public: the link is the secret, listed with its reason);
  - `GET /api/v1/sites/{site_id}/reports/feedback` (`sites.reports:read`, 30 days by default, at most 366).
- **The screens:**
  - `/f/{token}` (`src/web/feedback.py`, `feedback/answer.html`, `feedback.js`): an optional comment box, then
    five large score buttons;
  - the ticket page's question block (owner only, once done);
  - the join page's second consent question.
- **The sweep** (`src/core/scheduler.py`): `run_feedback_comment_retention_sweep`, nightly at 02:15 under lock
  887.

## Design notes

**"Exactly one request per visit" is three guards, not one hope.**
1. **The trigger.** A visit reaches `done` once, on its last leg: a transferred leg can never become done, and
   the lifecycle allows `in_progress → done` only once.
2. **The row.** `uq_visit_feedback_visit` refuses a second row for the visit, and the insert runs in a
   savepoint, so a racing second attempt returns nothing.
3. **The message.** The dedupe key `<visit id>:feedback` makes a second `notify` return the first row.

The test calls the hook again after `done` and gets `None`. It also takes a visit through a transfer and gets
one request, about the pharmacy leg.

**Why a separate consent, not the messages consent.** The consent text already on the platform
(`ConsentPurpose.FEEDBACK_SURVEY`) promises "one short message after my visit…", and the consent screen says
"the answer is no until you say yes". Sending a survey to everyone who agreed to hear about their turn would
break that promise. So the join page asks the second question in the same words as every channel. A patient
who says no gets a suppressed row, which counts toward nothing and whose link opens nothing.

**Screening before storage, not before display.** "Before any report shows it" is safest if the raw words are
never kept, so there is no raw column to leak later. The count of removed pieces is kept, so the report can
say something was taken out. The patterns catch numbers and addresses, not names, which is why comments are
also short-lived: they use the same window as the reason for a visit (`site.reason_retention_days`, 30 days by
default, ceiling 90) until Issue 95's data map sets the policy. The Issue 95 spec now lists this field.

**One tap means the tap that answers.** On SMS the answer is literally one keypress. A web push cannot carry
five choices (Chrome allows two notification actions), so the push opens the question page and one tap on a
score answers. The comment box sits above the scores, so a patient who wants to say something types first; one
who does not just taps. A patient who stays on their ticket page sees the question there. It is not shown on a
shared ticket link, because a relative must not answer for the patient.

**The per-staff view is named carefully.** It follows who marked the visit done, sorted by name, and carries a
note ("…not for ranking colleagues, and a small number of answers says very little"), the same stance as the
priority override counts (Issue 52).

## Changes

- **New:**
  - `src/database/models/feedback.py`, `alembic/versions/0041_visit_feedback.py`
  - `src/modules/appointments/feedback.py`, `feedback_router.py`, `feedback_schemas.py`
  - `src/web/feedback.py`, `src/templates/feedback/answer.html` and `missing.html`, `src/static/js/feedback.js`
  - `contracts/feedback.yaml`
- **Changed:**
  - Hooks and gates: `src/modules/queue/lifecycle.py` (the hook on done), `src/modules/patients/consent.py`.
  - Notifications and SMS: `src/core/log_redaction.py`, `src/core/webhook_gateways/africastalking.py`,
    `src/modules/notifications/patient_preferences.py` (`RATED`), `template_registry.py` (variables, the `/f/`
    push link), `src/locales/en/notifications.toml`, `notifications.lock.json`.
  - Enums: `src/commons/enums.py` (`FEEDBACK`, `TICKET_FEEDBACK`, `FeedbackRequestStatus`, `FeedbackChannel`,
    `AuditEntityType.VISIT_FEEDBACK`).
  - Wiring: `src/core/scheduler.py`, `src/api/v1/router.py`, `src/main.py`, `src/database/models/__init__.py`.
  - Screens: `src/modules/queue/ticket_page.py` and `schemas.py` (`TicketPageOut.feedback`);
    `queue/ticket.html`, `ticket.js` and `ticket.css`; `src/web/join.py`, `discover/join.html` and
    `patient-join.js`.
  - Contracts: `contracts/queue.yaml`, `notifications.yaml`, `README.md`.
- **Tests, new:**
  - `tests/integration/appointments/test_feedback.py` (8)
  - a screening test in `tests/unit/platform/test_logging_redaction.py`
- **Tests, updated:**
  - `test_quiet_hours.py`: the patient agrees to the survey too, so every event is the gate's to decide.
  - `test_transfer.py`: the journey now ends with one feedback message.
  - `tests/e2e/patient/test_join_page.py`: the first of two consent questions.
  - `test_cross_tenant.py`: the report is 404 for another clinic.
  - `test_api_route_gates.py`: the two public link routes, with reasons.
  - `test_site_scoped_queries.py`: four reasoned entries.
- **Docs:**
  - `docs/OPS/PATIENT_APP_TESTING.md` and `SMS_GATEWAY.md`;
  - the Issue 87 spec (files) and Issue 95's starting point (this comment field);
  - the M11 status row and bars (`--assume-closed 87`), the README Status block (76 of 111), and sprint 12's
    row, now in progress.

## Testing

- [x] `ruff check .`, `ruff format --check .` (658 files) and `mypy src/` (319 files) are clean.
- [x] **Migration:** `alembic check` on a database at 0041 reports "No new upgrade operations detected".
  `downgrade 0040` and `upgrade head` both run cleanly.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18 and Redis, with
  browsers, gave **2569 passed, 4 failed, 1 skipped, 9 xfailed**. Each failure was examined:
  - three were guards and tests seeing the new behaviour: the cross-tenant coverage check (`visitfeedback`
    needed a case), the transfer journey (its message list now ends with the feedback message), and the
    join-page browser test (the consent step has two questions);
  - one was `test_a_new_deploy_is_picked_up_on_the_next_launch`, which timed out at 30 s under the full load.
  All four then pass together (51 passed).
- [x] **How to verify, on a running server.** Playwright and curl drove the demo world on PostgreSQL
  (`clinicq_m11_demo` at 0041) through the real screens: four patients signed in by phone at Hillbrow, and
  reception moved each ticket to done.

  ```text
  1. ticket page: sent score=5 via=web
  2. link page: score=2 comment="Waited long but the sister was kind. Call me on [REDACTED:phone]." redactions=1
  3. SMS sent: BK ClinicQ: how was your visit to Hillbrow Community Health... today (ticket A011)? Reply 1 (very poor) to 5 (very good), then any comment.
     reply "4 quick and friendly" -> {"received":true,"outcome":"rated","event_id":"africas_talking:demo-87-2"}
     reply "1" (again)            -> {"received":true,"outcome":"ignored","event_id":"africas_talking:demo-87-3"}
  4. declined: suppressed
  5. manager@clinicq.example 200 {"overall": {"sent": 3, "answered": 3, "response_rate": 1.0, "average_score": 3.67},
     "distribution": {"1": 0, "2": 1, "3": 0, "4": 1, "5": 1}, "suppressed": 1, "answers_by_channel": {"sms": 1, "web": 2}}
     | comments: ['quick and friendly', 'Waited long but the sister was kind. Call me on [REDACTED:phone].']
     reception@clinicq.example 403
  ```

  - Patient 1 answered with one tap on their ticket page.
  - Patient 2 opened the link, typed a comment with their own number, and tapped 2.
  - Patient 3 replied to the SMS. The first attempt was sent from the patient's browser context with its
    session cookie, so CSRF refused it (403); a gateway sends no cookie, and the reply sent with curl was rated.
  - Patient 4 said no to the survey and was sent nothing.

  In the stored rows, the phone number exists only as `[REDACTED:phone]`.
- [x] **Over HTTP** (`tests/integration/appointments/test_feedback.py`, 8 passed, in UTC):
  - **Once per visit:** done sends one request (`sent`, `served_by` the staff member). The hook called again
    returns `None`. One notification per channel plan carries the dedupe key `<visit>:feedback` and the
    page `/f/<token>`.
  - **Transfer:** triage → pharmacy asks nothing at the transfer, and the pharmacy's done asks once, about that leg.
  - **Web:**
    - the link returns the question and scores 1–5;
    - the owner's ticket page carries the same `answer_url`, and a shared link carries none;
    - a score with a comment "…082 123 4567 or thandi@example.com" is stored as "…[REDACTED:phone] or
      [REDACTED:email]" with 2 redactions;
    - a second answer gets 409 `feedback.already_answered`;
    - the audit row names neither the number nor the words.
  - **SMS:** "5 thank you, my ID is 8001015009087" is rated 5 via `sms` with "…[REDACTED:id]". A later "3" is
    `ignored`.
  - **Consent and opt-out:** a patient who declined, and one who replied STOP, get `suppressed` rows and
    notifications. Their links are 404. The report shows sent 0, suppressed 2 and a null rate.
  - **Report:**
    - three sent and two answered give `{"sent": 3, "answered": 2, "response_rate": 0.667, "average_score": 4.5}`;
    - the distribution, Triage's slice, the staff member's slice and `{"web": 2}` all match;
    - the desk gets 403, the other clinic 404, and a backwards range 422.
  - **Retention and window:** the comment expires 30 days after the answer. The sweep empties it at that
    moment (not a minute before), keeps the score, and a second run does nothing. Answering at `expires_at`
    raises `FeedbackExpiredError`.
  - **Walk-in:** a walk-in with no patient is not asked.
- [x] **Unit:** screening masks a phone number, an e-mail address and a grouped ID number (3 removed) and leaves
  "Waited 45 minutes, room 4 was quick." alone.
- [x] **Notifications:** the notification suites pass, including the new template in one SMS segment, quiet hours
  holding it, and the editor's template count.

| Consent on the join page (390 px) | The ticket page asks (390 px) | Answered there | The link page, with a comment (390 px) | Thank you |
|---|---|---|---|---|
| ![Messages about your turn with a second question: Send me one short message after my visit, asking how it went, Yes ask me selected](https://github.com/Billykat7/clinicQ/blob/43bc9d6c3799293adfc13177b24a119545b51eb4/docs/GITHUB/PR/M11/assets/pr87/join-consent-feedback-390.png?raw=true) | ![A finished ticket, A010, with How was your visit today and five score buttons](https://github.com/Billykat7/clinicQ/blob/43bc9d6c3799293adfc13177b24a119545b51eb4/docs/GITHUB/PR/M11/assets/pr87/ticket-feedback-question-390.png?raw=true) | ![The question block saying Thank you. Your answer helps the clinic.](https://github.com/Billykat7/clinicQ/blob/43bc9d6c3799293adfc13177b24a119545b51eb4/docs/GITHUB/PR/M11/assets/pr87/ticket-feedback-thanks-390.png?raw=true) | ![The answer page with a typed comment including a phone number, the hint that such details are removed, and five score buttons](https://github.com/Billykat7/clinicQ/blob/43bc9d6c3799293adfc13177b24a119545b51eb4/docs/GITHUB/PR/M11/assets/pr87/answer-page-390.png?raw=true) | ![The answer page saying Thank you. Your answer helps the clinic.](https://github.com/Billykat7/clinicQ/blob/43bc9d6c3799293adfc13177b24a119545b51eb4/docs/GITHUB/PR/M11/assets/pr87/answer-page-thanks-390.png?raw=true) |

## Acceptance criteria

- [x] **Feedback is requested once per completed visit and never repeats:** the done hook, the unique visit
  row and the dedupe key each hold it, and the tests exercise all three, including a transferred visit.
- [ ] **Answering takes one tap or one keypress on every channel.** Partly: an SMS digit, a web tap on the link
  page and a tap on the patient's own ticket page are done and tested. A web push opens the page first,
  because a notification cannot carry five choices. WhatsApp replies wait for the inbound adapter (Issue 75),
  and the function it will call is here.
- [ ] **Scores aggregate per site and per queue in the M12 reports.** Partly: per site, queue, staff member and
  channel through `feedback_report` and `GET /sites/{id}/reports/feedback`, tested. The M12 report screens
  (Issues 89 and 94) are not built.
- [x] **Free-text comments are stored under the same retention rules as other patient text:** the clinic's
  `reason_retention_days`, emptied nightly, audited by count. The Issue 95 spec now names the field for its
  data map.
- [x] **Patients who opted out receive no request:** no survey consent or an SMS STOP gives a suppressed
  notification and no answerable link.
- [x] **Response rate is measurable so the team can judge whether the prompt works:** sent, answered and the
  rate, overall and per queue and staff member, excluding suppressed requests, are in the report.

## Risk and rollback

- **The done transition now also writes a feedback row and a message** for a patient's visit. It runs in the
  same transaction and savepoint pattern as the wait sample. A walk-in with no patient is untouched. Every
  lifecycle and queue test passes.
- **The join page's consent step has a second question.** Its answer is saved only when given, and the existing
  question is unchanged.
- **Public link routes:** each finds only its own request, refuses a suppressed one, and accepts one answer.
- **Rollback:** revert, then `alembic downgrade 0040`, which drops `visit_feedback`. Visits and tickets are
  untouched.

Closes #87
