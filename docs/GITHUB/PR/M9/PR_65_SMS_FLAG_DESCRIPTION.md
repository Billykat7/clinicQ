# PR: Gate SMS behind an `SMS_ENABLED` feature flag, off by default (Issue 65 follow-up / M9-65)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#65](https://github.com/Billykat7/clinicQ/issues/65) (SMS gateway, caps and kill switch), closed by
PR #189

SMS is the only transport that costs money per message. Until now it was always on: `SMS_PROVIDER` chose
where messages went, and only the operators' run-time kill switch could stop them. This PR adds a
deployment-level switch, an environment variable:

- **`SMS_ENABLED` defaults to `false`.** No environment sends SMS until someone sets `SMS_ENABLED=true`.
- **While it is off, no SMS reaches any provider**, whatever `SMS_PROVIDER` says:
  - a patient message goes by web push when the patient has a subscription;
  - otherwise the patient message's ledger row is `suppressed` with "no transport can reach the patient
    (SMS is switched off: SMS_ENABLED)";
  - any other SMS (a staff invitation's text) is `suppressed` with "SMS not sent: disabled".
- **Sign-in by SMS code refuses clearly.** `POST /api/v1/patients/otp/request` answers `503` with
  `patients.otp.sms_disabled` and issues no code, instead of silently sending nothing.
- **Opting out still works.** Delivery receipts and STOP replies are accepted with the flag off.
- **Operators can see it.** `GET /api/v1/notifications/sms/kill-switch` reports `sms_enabled` beside the
  switch.

**Decisions made here (agreed before the work):** the default is off, and sign-in answers `503` rather than
recording a code nobody receives.

**The kill switch stays.** The flag is for a whole deployment and needs a restart to change. The kill switch
stops SMS at run time with no deploy, and only matters while the flag is on.

## Summary

- **The setting:** `sms_enabled` in `src/core/config.py` (`SMS_ENABLED`, default `false`), with
  `.env.example` regenerated.
- **The gate:** `budget.check_sms` checks the flag first, before the kill switch and the caps, with the new
  `SmsBlockReason.DISABLED`. Every SMS goes through this check just before the gateway, on the first attempt,
  each retry and each fallback.
- **Patient transport planning:** `service.switched_off_channels()` leaves SMS out of the plan in `notify`
  and `_fall_back`. A web push with no SMS behind it therefore keeps its full retry budget, instead of the
  single attempt a transport with a fallback gets, and a failed push does not fall back to SMS.
- **Sign-in:** `patients.service.send_code` raises `SmsSignInUnavailableError` (`503`,
  `patients.otp.sms_disabled`) before counting the request or issuing a code.
- **What operators and patients read:**
  - `SmsKillSwitchOut.sms_enabled` (and `contracts/notifications.yaml`);
  - the ticket page's push fallback sentences say "keep this page open to see when it is your turn"
    instead of promising an SMS while SMS is off;
  - `docs/OPS/SMS_GATEWAY.md` §1 says what off means;
  - the unreleased `v0.9.0` note gains the upgrade step and the setting.
- **The tests:** `tests/conftest.py` sets `SMS_ENABLED=true` for the suite, which exercises the SMS path
  everywhere through fakes. A test of the flag builds its settings with `sms_enabled=False`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (302 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers and
  `AWS_S3_LOGGING_ENABLED=false`: **2389 passed, 2 failed, 1 skipped, 9 xfailed**. Both failures are
  timing-sensitive tests that this PR does not touch:
  - `test_nearby_search.py::test_an_open_now_search_also_stays_inside_the_budget` (a query-time budget);
  - `test_patient_app.py::test_a_new_deploy_is_picked_up_on_the_next_launch` (a browser test).

  Both passed when run again on their own (`2 passed`). The deploy test has now failed once before under
  full parallel load, while passing in CI every time.
- [x] `tests/integration/notifications/test_sms_flag.py`, 5 passed:
  - **the default:** `Settings(_env_file=None).sms_enabled` is `False` with the variable unset; `true`/`1`
    turn it on and `false`/`0` off;
  - **nothing reaches a provider:** an SMS-only patient's message is suppressed with the SMS-off reason, a
    kernel SMS is suppressed "SMS not sent: disabled", and the fake gateway received nothing;
  - **push keeps its retries:** a push that fails once is `failed` with the full attempt budget, and SMS was
    never attempted;
  - **sign-in:** `503`, `patients.otp.sms_disabled`, and no SMS;
  - **the kill switch answer** carries `sms_enabled: false`.
- [x] The notification, contract and unit suites with the flag on: **1418 passed**. Every existing SMS
  behaviour is unchanged.
- [x] **The ticket page with SMS off**, on the development server (`SMS_ENABLED` unset) as the ticket's own
  patient, after declining notifications. Before this change the page said "we will send you an SMS instead",
  which would no longer be true:

  | Push declined, SMS switched off (360 px) |
  |---|
  | ![The ticket page saying notifications are off and to keep the page open to see the turn](https://github.com/Billykat7/clinicQ/blob/e29ac9895c5595b598e889b1302e955295748efe/docs/GITHUB/PR/M9/assets/pr65-sms-flag/push-declined-sms-off.png?raw=true) |

## Risk and rollback

- **Deploying this turns SMS off wherever `SMS_ENABLED` is not set.** Patients without web push stop
  receiving messages, and sign-in by SMS code stops. **Set `SMS_ENABLED=true` in every environment that should
  send SMS before or with this deploy.** No environment is known to send real SMS yet (Issue 65's live account
  has not been set up).
- **No migration.** Rollback is a revert; SMS is then always on again.

Refs #65
