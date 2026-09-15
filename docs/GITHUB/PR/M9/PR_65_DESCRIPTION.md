# PR: SMS through a sandbox-first gateway, with daily caps, a kill switch, receipts and one segment per message (Issue 65 / M9-65)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#65](https://github.com/Billykat7/clinicQ/issues/65) · **Builds on:** #63 (the notification
service, PR #186) and #64 (web push, PR #188), both merged · **Unblocks:** #71, and #90's cost reports

SMS is the only transport that reaches every patient, and the only one that costs money per message. On a
student budget an unbounded send loop is a real financial risk, so this PR builds the brakes along with the
gateway:

- **An Africa's Talking-class gateway behind the existing `SmsProvider`.** It is the third implementation
  next to the logging and fake providers, not a new send path. It uses the **sandbox by default**, so local
  development spends nothing.
- **Daily caps per clinic and per patient.** Reaching one stops those SMS, records each refused message with
  its reason, and **alerts the team once**.
- **A kill switch** that stops every SMS on the very next message, flipped over HTTP with no deploy.
- **Delivery receipts** that move a message to `delivered` or `dead`, once, however often the gateway
  repeats them.
- **Every message's cost** is recorded from what the gateway reported, and readable per clinic per day.
- **No silent splitting.** Every queue SMS is written to fit one segment, even with a 200-character clinic
  name, and look-alike characters are sent as GSM 7-bit. A message that would still run past
  `SMS_MAX_SEGMENTS` is refused rather than sent in three paid parts.

**Not done here, and not claimed:**

- **Nothing has been sent through a real Africa's Talking account, sandbox or live.** The adapter is
  tested against the API's documented request and answer, played by `httpx.MockTransport`. The first run
  against the sandbox account is an operator step (`docs/OPS/SMS_GATEWAY.md` §2).
- **"Every template in all five languages" is checked for the languages that exist, which is English.**
  The test runs over `templates.SMS_LANGUAGES`, and #66 and #77 add the other four to that same list, so
  the check covers them when they land.
- **The receipt webhook is not signed.** Africa's Talking does not sign callbacks, so a secret in the
  callback URL takes the place of a signature (see *Design notes*).
- **No screen for the caps or the switch.** Both are API routes, documented for operators. A clinic
  settings panel belongs with the M12 reporting screens.

## Summary

- **The gateway** (`src/modules/notifications/sms.py`):
  - `AfricasTalkingSmsProvider` POSTs to `/version1/messaging` with the account's username and the
    `apiKey` header, and reads the recipient's `statusCode`, `messageId` and `cost` (`"ZAR 0.2500"`).
  - Statuses `403`, `404`, `406` and `409` (invalid or unsupported number, opted out at the gateway,
    do-not-disturb) are `SmsRecipientRejectedError`, never retried.
  - Anything else, a timeout or an HTTP error is `SmsSendError`, retried with backoff.
  - In sandbox the base URL and username are the sandbox's, and no sender id is sent.
  - `SmsProvider.send_message` returns an `SmsReceipt` (id, cost, currency). The default wraps `send` and
    `cost_of`, so the logging and fake providers are unchanged.
- **Settings** (`src/core/config.py`):
  - new: `AFRICAS_TALKING_USERNAME`, `AFRICAS_TALKING_API_KEY` (secret), `SMS_SANDBOX` (default true),
    `SMS_SITE_DAILY_CAP` (300), `SMS_PATIENT_DAILY_CAP` (8), `SMS_MAX_SEGMENTS` (2) and `SMS_WEBHOOK_TOKEN`
    (secret);
  - the configuration guard refuses the gateway with no key, and live sending with the sandbox username;
  - `.env.example` is regenerated.
- **Length and encoding** (`sms_segments.py`, new):
  - `count_segments` measures GSM 7-bit (including the two-septet extension characters) and UCS-2 exactly;
  - `to_gsm7` replaces dashes, curly quotes, ellipses, odd spaces and the Afrikaans `ê ë ô û î` with GSM
    characters;
  - `SmsTransport` normalises, counts, refuses anything over `SMS_MAX_SEGMENTS` as `SmsTooLongError`
    (permanent), and records what the gateway charged, in its currency.
- **One segment per queue SMS** (`templates.py`): clinic names are shortened to 28 characters and queue or
  room names to 18 (with `...`), and the recall, no-show and transfer wordings are tightened. The recall
  still says it called once more, where to come and within how many minutes, and that the ticket is marked
  missed otherwise.
- **The brakes** (`budget.py`, new):
  - `check_sms` runs just before the gateway in `service.attempt`, for every SMS: queue messages, sign-in
    codes and invitations;
  - the kill switch applies to all of them, the clinic's cap to rows with a clinic, and the patient's cap
    to rows with a patient;
  - a refused row is `suppressed` with `SMS not sent: <reason>`;
  - the first refusal at a cap each day inserts an `SmsCapAlert` and posts a team alert;
  - on PostgreSQL the count and the send for one clinic run under a transaction-level advisory lock;
  - `site_budget` returns the cap, count, what is left and the spend for a day;
  - `set_sms_kill_switch` stores who flipped the switch and why, and posts an alert.
- **Tables** (migration `0034`): `site.sms_daily_cap` (nullable, at least 0), `platform_switch`,
  `sms_cap_alert` (unique by reason, subject and day) and `sms_delivery_event` (primary key
  `provider:message id:status`).
- **Receipts** (`src/core/webhook_gateways/africastalking.py`, new;
  `POST /api/v1/webhooks/sms/africastalking/{token}`):
  - the token is checked in constant time before the body is read: a wrong one is `404`, and the webhook
    being off is `503`;
  - the form body is parsed, and one that is not a receipt is `400`;
  - the event is recorded, then applied through `service.apply_sms_receipt`: `Success` becomes
    `delivered`; `Failed`, `Rejected`, `Expired` and `AbsentSubscriber` become `dead` with the reason; the
    in-transit statuses change nothing, and nothing moves backwards;
  - the answer is `processed`, `duplicate` or `ignored`;
  - the request log masks the token (`log_redaction.py`).
- **Routes:**
  - `GET` and `PUT /api/v1/notifications/sms/kill-switch` (`logs` read and update);
  - `GET` and `PUT /api/v1/sites/{site_id}/sms-budget` (`sites.settings` read and update, audited;
    `budget_router.py`).
- **Docs:** `docs/OPS/SMS_GATEWAY.md` covers setup, sandbox, receipts and their secret, caps, the switch and
  message length. `RUNBOOK_ALERTS.md` gains *An SMS cap was reached* and *The SMS kill switch was flipped*.

## Design notes

**A secret URL instead of a signature, said plainly.** The spec asks for signed receipts following the payment
webhooks. Africa's Talking does not sign callbacks, so there is nothing to verify. Everything else in that
pattern is followed: the check runs before the body is parsed, the answer does not reveal whether the path
exists, and events are idempotent rows. The secret is 256 random bits in the callback URL, compared in constant
time. Its weakness is that URLs are logged: ClinicQ masks it in its own logs, but the reverse proxy's access
log does not, and `SMS_GATEWAY.md` says so. A gateway that signs would get a real signature check in its own
gateway module.

**A failed delivery is dead, not retried.** The gateway charges when it accepts a message. Sending again to a
phone that was off or out of coverage costs a second time and usually fails the same way. The patient still has
web push, the ticket page and the board.

**What counts against a cap is what was billed.** The count uses `sent_at` (the gateway accepted the message)
in the Johannesburg day, including messages whose delivery later failed. Refused messages do not count.

**The kill switch stops sign-in codes too.** That is what an emergency brake means, and `SMS_GATEWAY.md` and
the runbook say so.

**Guards updated, with reasons:**

- `test_api_route_gates` lists the receipt webhook as public;
- `test_site_scoped_queries` names `apply_sms_receipt`;
- the sites contract excludes `/sites/{site_id}/sms-budget` for #71's notifications contract;
- the cross-tenant suite's `PENDING` entry for `notification` is now a real case: clinic A's manager asks
  for clinic B's budget and gets 404.

## Changes

- **New:**
  - `src/modules/notifications/budget.py`, `budget_router.py`, `sms_segments.py`
  - `src/core/webhook_gateways/africastalking.py`, `src/database/models/sms_budget.py`
  - `alembic/versions/0034_sms_budget.py`, `docs/OPS/SMS_GATEWAY.md`
- **Changed:**
  - `sms.py` (the gateway, `SmsReceipt`, `send_message`), `transports/sms.py`, `transports/base.py`
    (currency)
  - `service.py` (the budget check, `apply_sms_receipt`), `templates.py` (one-segment wordings,
    `SMS_LANGUAGES`), `schemas.py`, `router.py`
  - `src/api/v1/routes/webhooks.py`, `src/api/v1/router.py`, `src/schemas/webhooks.py`
  - `src/core/log_redaction.py`, `src/core/config.py`, `src/database/models/site.py`, `enums.py`
    (`SmsBlockReason`, `PlatformSwitch`, `SmsDeliveryState`, `SmsProviderKind.AFRICAS_TALKING`)
  - `.env.example`, `RUNBOOK_ALERTS.md`
- **Tests, new:**
  - `tests/integration/notifications/test_sms_caps.py` (13)
  - `tests/unit/notifications/test_sms_segments.py` (18)
- **Tests, updated:** `test_cross_tenant.py`, `test_api_route_gates.py`, `test_site_scoped_queries.py` and
  `test_openapi_contracts.py`.
- **Docs:** the Issue 65 spec (files), the M9 status row and progress bars (`--assume-closed 65`), and the
  README Status block (66 of 109).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (294 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2174 passed,
  1 skipped, 9 xfailed, 0 failed**.
- [x] **How to verify, steps 1 to 3**, run as a script on PostgreSQL (so the clinic's advisory lock is the
  real one), with the fake gateway charging R0.25:

  ```text
  STEP 1: Glen Earle Clinic's daily cap set to 3; four patients are called
    SMS 1: status=sent       cost=0.2500 ZAR
    SMS 2: status=sent       cost=0.2500 ZAR
    SMS 3: status=sent       cost=0.2500 ZAR
    SMS 4: status=suppressed cost=None  SMS not sent: site_daily_cap
    budget: cap=3 sent=3 remaining=0 spend=0.7500 ZAR
    team alerts posted: 1: 💸 SMS cap reached: Glen Earle Clinic has sent its 3 SMS for 2026-09-15. Further SMS to its patients are not sent today; web push still is (docs/CICD/RUNBOOK_ALERTS.md, “An SMS cap was reached”).

  STEP 2: the kill switch
    queue SMS 0.152 s after the switch: status=suppressed SMS not sent: kill_switch
    sign-in code: status=suppressed SMS not sent: kill_switch
    gateway received after the switch: 0 messages
    alerts: ['🛑 SMS kill switch ON: no SMS will be sent (by ops@clinicq.example: demo).', '✅ SMS kill switch OFF: SMS is sending again (by ops@clinicq.example: demo over).']

  STEP 3: every queue SMS through the segment counter (languages: ('en',) ) with a 200-character clinic name
    [en] next         115 chars gsm7 1 segment
    [en] called       104 chars gsm7 1 segment
    [en] recalled     156 chars gsm7 1 segment
    [en] no_show      142 chars gsm7 1 segment
    [en] transferred  153 chars gsm7 1 segment
    [en] cancelled    128 chars gsm7 1 segment
  ```

  The segment test found a real overflow first: the transfer message was **162** characters with the
  longest names. It was shortened before this run.
- [x] **Over HTTP** (`test_sms_caps.py`, 13 passed):
  - the cap is set by the clinic manager, a receptionist gets 403, and the 4th and 5th SMS are refused with
    one alert, with `GET /sms-budget` reading `sent 3, remaining 0, spend 0.7500 ZAR`;
  - a patient's cap of 2 refuses the 3rd and 4th;
  - the kill switch is flipped by an `admin` (a clinic manager gets 403). In the test run the next SMS was
    refused *first SMS after the switch refused 17 ms after it was flipped*, sign-in code included, and
    switching it off sends again;
  - receipts: `Success` gives `delivered`, and the same receipt again is `duplicate`; `Buffered` changes
    nothing; `Failed` with `AbsentSubscriber` gives `dead`; an unknown id is `ignored`; a wrong token is 404
    and changes nothing; a non-form body is 400; the webhook off is 503;
  - the adapter sends to `https://api.sandbox.africastalking.com/version1/messaging` as `sandbox`, with the
    `apiKey` header and no sender, and in live mode to `api.africastalking.com` with the username and
    sender id. Gateway statuses 403 and 406 are refused as bad numbers; 405 and 500 are retryable;
  - the reported cost `ZAR 0.3100` lands on the row;
  - a 360-character message is refused as `too_long: 3 gsm7 segments` without reaching the gateway;
  - an en dash in a wait range reaches the gateway as a hyphen.
- [x] Migration `0034`: the full suite's Alembic tests upgrade, round-trip and find nothing for autogenerate.
- [ ] A real Africa's Talking sandbox or live account: not used.
- [ ] Screenshot: no template, stylesheet or script changes.

## Acceptance criteria

- [x] **Hitting a cap stops sends and raises an alert rather than failing silently:** each refused message is
  on the ledger with the reason, and one team alert goes out per clinic or patient per day (step 1, and the HTTP
  tests).
- [x] **The kill switch stops all SMS within one minute, without a deploy:** a database switch read on every
  SMS, flipped over HTTP. The next SMS was refused 17 ms (test) and 0.152 s (script) after the flip, sign-in
  codes included.
- [x] **Delivery receipts update the notification log to a terminal status:** `delivered` or `dead`,
  idempotently. The in-transit statuses change nothing, which leaves the row at `sent`, itself terminal (#63).
- [x] **Every message's cost is recorded and reportable per site:** `cost` and `cost_currency` come from the
  gateway's answer, and `GET /api/v1/sites/{site_id}/sms-budget` reports the day's spend, which #90 reads.
- [ ] **Messages stay within one SMS segment, verified for every template:** met for every patient message in
  every language the templates have, with the longest names the database allows (step 3), and enforced at send
  time (`SMS_MAX_SEGMENTS`, look-alike characters normalised). Not yet in five languages: the other four arrive
  with #66 and #77, and the same test covers them then.
- [x] **Sandbox mode allows full local development with no spend:** `SMS_SANDBOX` defaults to true and sends
  to the sandbox as `sandbox`; with no account at all, the logging provider and `/dev/outbox` run the whole flow.

## Risk and rollback

- **Default caps are live on merge:** 300 SMS a clinic a day and 8 a patient. On today's logging provider
  nothing is spent either way. A clinic busy enough to need more gets a per-clinic cap from its manager.
- **Queue SMS wording changes**, shortened to fit one segment. The meaning of each message is kept, and the
  recall and no-show tests still check what they say.
- **A new public webhook**, which reads nothing until the token matches and applies each receipt once.
- **Migration `0034`** adds one nullable column and three tables. The previous release ignores them.
- **Rollback** is a revert and a downgrade to `0033`. Caps, the switch and receipt history go with it, and
  SMS sends as before.

Closes #65
