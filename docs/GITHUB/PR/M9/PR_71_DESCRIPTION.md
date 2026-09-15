# PR: The notifications contract, every failure path ending terminal, and a delivery alert (Issue 71 / M9-71)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#71](https://github.com/Billykat7/clinicQ/issues/71) · **Builds on:** #63, #68, #64, #65, #66, #67,
#69 and #70 (PRs #186 to #193), all merged · **Closes M9**, with the
[`v0.9.0` release note](../../RELEASES/RELEASE_v0_9_0.md)

A message that is never sent looks exactly like a message nobody replied to. This PR makes that failure loud,
and re-verifies M9's notification promises from the shipped code rather than from the earlier PRs' tests:

- **The contract.** `contracts/notifications.yaml` documents all 27 notification operations with their error
  responses. Issue 30's drift test checks it against the application, and a new test drives every one of the
  75 documented error statuses over real HTTP.
- **Every failure path ends in a terminal ledger status.** Tested with the real Africa's Talking and web push
  adapters and only the network faked: a provider timeout, a provider error, a malformed number, a revoked
  push subscription, and an error nobody planned for.
- **That last test found a real hole, and this PR fixes it.** An error before the transport was reached (a
  bug rendering the message, say) escaped the attempt. The row stayed `queued` with no attempt counted, the
  sweep met the same error on every run, and every row after it in that sweep waited too.
- **A replayed queue event sends exactly one message**, however it is replayed.
- **Delivery rate and cost per transport** are on the operators' notifications page.
- **The team is alerted** when a transport's failure rate crosses its threshold, once per transport per
  window.
- **No notification test can reach a real provider or needs a credential.** A fixture fails any request that
  leaves the machine, naming the host.

## Summary

- **The contract** (`contracts/notifications.yaml`, 22 paths, 27 operations):
  - every `/notifications` route: the ledger, the notification centre, account preferences, patient
    preferences by link, the SMS kill switch, the templates and their editor, web push key and
    subscriptions, the delivery webhook and the new `delivery-stats`;
  - a clinic's `/sites/{site_id}/sms-budget`, and the two SMS gateway callbacks;
  - each with who may call it, its schemas and examples, and every error with its `code` where the handler
    sets one.
  - The harness entry (`test_openapi_contracts.py`) owns these by pattern, so the sites contract cedes
    `sms-budget` automatically, and its old exclusion is gone. Two new tests there check the ownership:
    exactly these routes and not the payment webhooks, and no served route with two contracts.
- **The error proof** (`tests/integration/contracts/test_notifications_contract.py`, 84 tests):
  - 81 cases, each a request against the real app that produces one documented status, checking the status,
    the envelope `code`, and that the contract names that code for that response;
  - it fails when the contract documents an error with no case, and when a case proves a status the contract
    does not document, and a self-check shows both comparisons can fail.
  - Covered: 401 unauthenticated; 403 without the `logs` grant or without the CSRF token; 404 unknown ids,
    templates, another clinic's budget, an unknown ticket link or a wrong callback secret; 409 web push not
    configured; 422 handler and validation refusals; 400 and 503 on the gateway callbacks.
- **The hole, fixed** (`service.attempt_or_record`, new):
  - every delivery path (the post-commit delivery, the retry sweep, the fallback, `send_sms`) calls it;
  - it runs `attempt` in a savepoint; if anything raises, the partial changes are undone and the error is
    recorded as a failed attempt (`unexpected error: KeyError: …`), so the row retries with backoff and is
    dead-lettered at its budget like any other failure, and the sweep carries on with the next row;
  - `attempt` itself is unchanged, so the #67 gate guard still sees `resolve` before the transport.
- **Delivery health** (`src/modules/notifications/delivery_stats.py`, new):
  - `channel_stats` counts one window's messages per transport by status, with their cost;
  - `failure_rate` is dead out of attempted (sent, delivered or dead); suppressed and waiting messages count
    neither way;
  - `watch_failures` alerts through the team channel of Issue 14 when a transport has at least
    `NOTIFICATION_FAILURE_ALERT_MIN_ATTEMPTS` (20) attempted messages in the last
    `NOTIFICATION_FAILURE_ALERT_WINDOW_MINUTES` (60) and at least `NOTIFICATION_FAILURE_ALERT_RATE` (25%)
    died;
  - once per transport per window, recorded in `notification_failure_alert` (migration `0037`), unique by
    transport and window start.
  - `notification_failure_watch` runs it every `NOTIFICATION_FAILURE_WATCH_MINUTES` (5) under advisory lock
    771.
- **The panel:** `GET /api/v1/notifications/delivery-stats?hours=` (`logs` read), and **Delivery by
  transport** on `/admin/notifications` (`admin-notification-health.js`). It shows sent, delivered, failed,
  not sent, waiting, failure rate and cost, over the last hour, day or week, marks a transport over the alert
  rate, and refreshes every minute. The list's channel filter gains web push and WhatsApp.
- **The runbook:** `docs/CICD/RUNBOOK_ALERTS.md`, "Notifications are failing": what to read first, and what
  a provider outage, bad addresses and an unexpected error each look like.

## Design notes

**Why re-verify with the real adapters.** The earlier PRs tested failures with `NoopTransport`, which raises
exactly the error each test asks for. That proves the service handles the errors a transport promises to
raise, but not that the adapters raise them. Here, the Africa's Talking adapter meets a real `ReadTimeout`, a
real `500` and a real `InvalidPhoneNumber` answer, and the web push sender meets a real `410`. So the mapping
from what a provider does to what the ledger says is tested end to end.

**Why a savepoint rather than more `try` blocks.** The hole was anything raised before the transport's own
`try`: rendering, preferences, the SMS budget, reading the patient. Wrapping each would miss the next one
added. One savepoint around the whole attempt, at the one entry every delivery path uses, covers them all,
and undoes whatever the failed attempt half-wrote before the failure is recorded.

**Why the alert measures a rolling window but deduplicates on a fixed one.** The rate is measured over the
last 60 minutes whenever the watch runs, so a burst is seen within 5 minutes. The record that stops repeats is
keyed to the clock hour the run falls in, so a sustained outage alerts once an hour, not every 5 minutes. Two
instances racing insert the same key, and only one posts.

**Why a network guard, not just "no credentials in CI".** CI has no credentials, but the first draft of the
timeout test ran its retry sweep outside the fake transport and quietly called Africa's Talking's sandbox with
a made-up key (it got `401`). A missing credential does not stop a request; the guard does, and names the
host.

## Changes

- **New:**
  - `contracts/notifications.yaml`
  - `src/modules/notifications/delivery_stats.py`
  - `src/database/models/notification_failure_alert.py`, `alembic/versions/0037_notification_failure_alert.py`
  - `src/static/js/admin-notification-health.js`
  - `docs/GITHUB/RELEASES/RELEASE_v0_9_0.md`
- **Changed:**
  - `src/modules/notifications/service.py` (`attempt_or_record`), `router.py` (`delivery-stats`),
    `schemas.py` (`DeliveryStatsOut`, `ChannelDeliveryStatsOut`)
  - `src/core/scheduler.py`, `src/core/config.py` (four `NOTIFICATION_FAILURE_*` settings), `.env.example`,
    `src/database/models/__init__.py`
  - `src/templates/admin/notifications.html`, `src/static/css/admin.css`
  - `contracts/README.md`, `docs/CICD/RUNBOOK_ALERTS.md`
- **Tests, new:**
  - `tests/integration/contracts/test_notifications_contract.py` (84)
  - `tests/integration/notifications/test_notification_failures.py` (6)
  - `tests/integration/notifications/test_notification_replay.py` (3)
  - `tests/integration/notifications/test_delivery_health.py` (4)
- **Tests, updated:**
  - `tests/integration/contracts/test_openapi_contracts.py`: the notifications entry and two ownership tests;
  - `tests/integration/notifications/conftest.py`: `no_provider_is_reached`, for every notification test;
  - `tests/unit/security/test_site_scoped_queries.py`: `delivery_stats.channel_stats` is platform-wide, with
    its reason.
- **Docs:** the Issue 71 spec (files); the M9 milestone marked done, with each exit criterion's evidence;
  progress bars (`--assume-closed 71`: 71 of 109, 9 of 14 milestones); the README Status block.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (302 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2378 passed,
  2 failed, 1 skipped, 9 xfailed**. Both failures were browser tests:
  `test_dashboard.py::test_call_next_answers_at_once_and_a_double_click_calls_one_patient` and
  `test_patient_app.py::test_a_new_deploy_is_picked_up_on_the_next_launch`. I had started a second test run
  alongside the suite, and both passed when run again on their own (`2 passed`). A run of the whole suite
  without the contracts directory, just before, had every browser test passing (2257 passed). Neither test's
  code is touched here.
- [x] The contract and its proof: `pytest tests/integration/contracts` **123 passed**, including
  `test_notifications_contract.py` (84).
- [x] **How to verify, step 1 (each failure ends terminal):** `test_notification_failures.py`, 6 passed:
  - `provider-timeout`: 4 attempts with backoff, then `dead`, "gateway unreachable: ReadTimeout";
  - `provider-error`: 4 attempts, then `dead`, "gateway answered HTTP 500";
  - malformed number: one attempt, `dead`, "gateway refused the number: InvalidPhoneNumber", one call
    to the gateway;
  - revoked subscription: push `dead` after one `410`, the subscription deleted, the message sent by SMS,
    and the next message straight to SMS with no second push;
  - unplanned error: `dead` after 4 attempts with "unexpected error: KeyError: …", while another patient's
    message in the same sweeps was sent. **Before `attempt_or_record` this test failed**: the `KeyError`
    escaped `run_retry_sweep`;
  - each test ends by requiring no row anywhere left `queued` or `failed`.
- [x] **How to verify, step 2 (replay):** `test_notification_replay.py`, 3 passed:
  - the call and the next-in-line look replayed three times: one `ticket_called` and one `ticket_next`
    row, each sent once, two messages in total;
  - the recall sweep run twice for the same moment, plus a hand replay: one recall, one message;
  - `deliver_committed` twice for a sent row, then the sweep: one send and one row.
- [x] **How to verify, step 3 (no credentials):** with `AFRICAS_TALKING_API_KEY`, `SMS_WEBHOOK_TOKEN`,
  `SMTP_PASSWORD`, both `WEB_PUSH_VAPID_*` keys, `NOTIFICATION_WEBHOOK_SECRET` and `TEAM_WEBHOOK_URL` set
  empty and `SMS_PROVIDER=logging`, `pytest tests/integration/notifications tests/unit/notifications
  tests/integration/contracts` gave **284 passed**. The local `.env` has an SMTP password, which is why they
  were set empty rather than only unset. `test_the_suite_runs_with_no_provider_credentials_and_cannot_reach_a_provider`
  also shows the guard stopping an unfaked gateway call, naming `api.sandbox.africastalking.com`.
- [x] **The alert** (`test_delivery_health.py`, 4 passed):
  - 10 of 30 SMS dead (33%) alerts once, a second run in the same hour stays quiet, and the next hour
    alerts again;
  - 19 of 99 failed (19%), and a high rate on too few attempts, alert nobody;
  - the watch is registered once on the scheduler.
  - **Against PostgreSQL**, through the scheduler's own entry point on the development database seeded with
    a failing hour:

  ```text
  WARNING src.core.team_alerts: Team alert: 📉 Notifications failing: 13 of 37 sms messages dead-lettered in the last 60 minutes (35%, the threshold is 25%). Check the provider, then the ledger at /admin/notifications. Runbook: docs/CICD/RUNBOOK_ALERTS.md, "Notifications are failing".
  first run alerted: [<NotificationChannel.SMS: 'sms'>]
  second run alerted: []
  ```

- [x] **The panel** on the development server, as the operator, with no console errors. On the last hour,
  SMS is marked "Over the alert rate"; the API side is `test_the_dashboard_figures_are_the_ledgers_per_transport`,
  with every count, the rate, the cost, the window and 401/403/422.

| The notifications page, last 24 hours | Last hour: SMS over the alert rate |
|---|---|
| ![Delivery by transport above the notification list, per transport sent, delivered, failed, not sent, waiting, failure rate and cost](https://github.com/Billykat7/clinicQ/blob/e1970614eda95bde25ca988879fa6a43722277fa/docs/GITHUB/PR/M9/assets/pr71/notifications-page.png?raw=true) | ![The panel on the last hour with SMS at 35.1% marked over the alert rate](https://github.com/Billykat7/clinicQ/blob/e1970614eda95bde25ca988879fa6a43722277fa/docs/GITHUB/PR/M9/assets/pr71/delivery-panel-last-hour.png?raw=true) |

## Acceptance criteria

- [x] **The contract documents every notification route including error responses:** 27 operations, 75
  error statuses, the drift test in both directions, and every error status driven over HTTP.
- [x] **Every failure path ends in a terminal log status, never in limbo:** timeout, error, malformed number,
  revoked subscription and an unplanned error each end `dead` (or fall back to a transport that sends), with
  no row left `queued` or `failed` after the sweep. The unplanned case was a real hole, fixed here.
- [x] **A replayed queue event sends exactly one notification:** the call, "you are next", the recall sweep
  and the post-commit delivery, each replayed, one message each.
- [x] **Delivery rate and cost per transport are visible on a dashboard:** **Delivery by transport** on
  `/admin/notifications`.
- [x] **Tests run with no real provider credentials:** the fixture settings carry none, the guard stops any
  request leaving the machine, and the notification and contract suites pass with every provider variable
  unset (below).
- [x] **An alert fires when the delivery failure rate crosses a threshold:** once per transport per window,
  in tests and against PostgreSQL through the scheduler's own entry point.

## Risk and rollback

- **The retry sweep behaves differently only when something unexpected raises.** Before, the sweep stopped
  and the row stayed queued forever; now the row is recorded as a failed attempt and the sweep continues. A
  genuine bug therefore shows up as `dead` rows and, at volume, an alert, instead of silence.
- **A new team alert.** The threshold (25% of at least 20 attempted messages in an hour) is set to stay quiet
  at low volume. Every value is a setting.
- **Migration `0037`** adds one table. The previous release ignores it.
- **Rollback** is a revert and a downgrade to `0036`. The alert history is lost, and the sweep goes back to
  stopping on an unexpected error.

## Found while writing the contract, not changed here

- **The SMS gateway callbacks answer `400`, `404` and `503` with a bare `detail`**, without the envelope's
  `code` and `request_id` that every other error carries. Documented as they are.
- **An unconfigured delivery webhook answers `404`, and an unconfigured SMS callback `503`.** Documented as
  they are.
- **A bounce on the generic delivery webhook sets `failed`, which the sweep retries**, while an SMS receipt's
  failure sets `dead`. It still ends terminal at the attempt budget, but a bounced email may be re-sent. Worth
  its own fix.
- **`POST /notifications/web-push/subscriptions` answers `200` for a browser subscribing again**, which
  FastAPI's document omits (it knows only `201`). The contract documents both.
- **The other contracts name the staff cookie `bk_clinicq_access`**; it is `bk_clinicq_access_token`. The new
  contract uses the real names.
- **On a developer machine whose `.env` turns S3 logging on**, test runs upload their warning logs to that
  bucket. Nothing in this PR causes it, but it is worth switching off for local test runs.

Closes #71
