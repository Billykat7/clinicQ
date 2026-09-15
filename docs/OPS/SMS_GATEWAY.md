# SMS: the gateway, what it may cost, and the kill switch

SMS is the only way to reach every patient, and the only way that costs money per message. This page covers
setting up the gateway (Africa's Talking or one like it), sandbox development with no spend, the daily caps,
receipts, what a message costs, and how to stop every SMS in an emergency.

## 1. Setting up the gateway

ClinicQ sends SMS through whichever provider `SMS_PROVIDER` names:

| `SMS_PROVIDER` | What it does | Spend |
|---|---|---|
| `logging` (default) | Nothing leaves the server; in development the message appears at `/dev/outbox` | None |
| `africas_talking` | Africa's Talking's messaging API | Sandbox: none. Live: per segment |
| `fake` | The test suite's in-memory gateway | None |

For Africa's Talking:

| Setting | Value | Secret? |
|---|---|---|
| `SMS_PROVIDER` | `africas_talking` | No |
| `AFRICAS_TALKING_API_KEY` | From the Africa's Talking dashboard (sandbox and live accounts have different keys) | **Yes** |
| `AFRICAS_TALKING_USERNAME` | The live account's username; ignored in sandbox | No |
| `SMS_SANDBOX` | `true` (default) or `false` for live sending | No |
| `SMS_FROM` | The approved sender id; ignored in sandbox | No |

The app refuses to start with `SMS_PROVIDER=africas_talking` and no API key, and with live sending on while
the username is still `sandbox`.

## 2. Sandbox: full local development with no spend

`SMS_SANDBOX` is `true` unless an environment turns it off. In sandbox, ClinicQ sends to
`api.sandbox.africastalking.com` as the `sandbox` account:

- the gateway's simulator shows the message (open the simulator in the Africa's Talking sandbox dashboard
  and register a test phone number there);
- nothing reaches a real phone, and nothing is charged;
- delivery receipts work the same way, if the callback in §3 is set up for the sandbox app.

Without any account at all, keep `SMS_PROVIDER=logging`: the whole flow runs, and in development the
message text is at `GET /dev/outbox`.

## 3. Delivery receipts

The gateway calls back every time a message's delivery changes. ClinicQ records each receipt once and moves
the message on the delivery ledger:

- `Success` makes it `delivered`;
- `Failed`, `Rejected`, `Expired` and `AbsentSubscriber` make it `dead`, with the reason. It is **not**
  sent again automatically, because the gateway has already charged for it and a second paid attempt to
  an unreachable phone rarely does better;
- `Sent`, `Submitted` and `Buffered` change nothing.

**Africa's Talking does not sign its callbacks.** ClinicQ therefore puts a secret in the callback URL and
checks it before reading anything:

1. Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Set it as `SMS_WEBHOOK_TOKEN` in the environment's secrets.
3. In the Africa's Talking dashboard, under SMS delivery reports, set the callback URL to
   `https://<ClinicQ's address>/api/v1/webhooks/sms/africastalking/<the secret>`.

A call with the wrong secret gets `404`, exactly like a path that does not exist. ClinicQ's own request
log masks the secret. **The reverse proxy's access log does not**, so restrict who can read it. To rotate
the secret, set a new `SMS_WEBHOOK_TOKEN` and update the dashboard in the same few minutes; receipts sent in
between are lost, which leaves those messages `sent` rather than `delivered`.

With `SMS_WEBHOOK_TOKEN` unset the endpoint answers `503` and receipts are off.

## 4. What SMS may cost

Every SMS passes three checks just before it is handed to the gateway, after consent and preferences. A
message refused here is recorded on the ledger as `suppressed` with the reason, never dropped silently.

| Check | Default | Change it |
|---|---|---|
| **Kill switch** | Off | `PUT /api/v1/notifications/sms/kill-switch` (below) |
| **Clinic's daily cap** | `SMS_SITE_DAILY_CAP` = 300 | Per clinic: `PUT /api/v1/sites/{site_id}/sms-budget` `{"daily_cap": 500}` (a clinic manager); `null` goes back to the default |
| **Patient's daily cap** | `SMS_PATIENT_DAILY_CAP` = 8 | Environment setting |

- A day is the Johannesburg day. What counts is what the gateway accepted, because that is what it bills.
- The first refusal at a cap posts a team alert, once per clinic or patient per day. The runbook entry is
  [RUNBOOK_ALERTS.md](../CICD/RUNBOOK_ALERTS.md), *An SMS cap was reached*.
- `GET /api/v1/sites/{site_id}/sms-budget?day=2026-09-15` shows a clinic's cap, count, what is left, and
  spend. The M12 reports read the same numbers.
- Each message's cost is recorded on its ledger row, from what the gateway reported (`cost`,
  `cost_currency`).

### The kill switch

It stops **every** SMS on the platform on the very next message, sign-in codes included, with no deploy and
no restart: the switch is read from the database for each SMS. It needs an account holding `logs` update (the
platform `admin` role).

```bash
curl -X PUT https://<ClinicQ>/api/v1/notifications/sms/kill-switch \
  -H "Content-Type: application/json" -H "X-CSRF-Token: <token>" -b "<admin session cookies>" \
  -d '{"enabled": true, "reason": "unexpected spend at the gateway"}'
```

Turn it off with `{"enabled": false}`. Either change posts a team alert naming who and why.
`GET /api/v1/notifications/sms/kill-switch` shows the current state. Messages refused while it was on are
not re-sent when it goes off.

## 5. Message length

A gateway bills per segment. One segment is 160 characters in the GSM 7-bit alphabet, but only 70 characters
once any character outside it appears (a curly quote, an en dash, an Afrikaans "ê").

- Before sending, ClinicQ replaces look-alike characters with GSM ones: dashes become `-`, curly quotes
  become straight ones, and the Afrikaans `ê ë ô û î` lose their accents.
- ClinicQ then counts the segments, and refuses a message longer than `SMS_MAX_SEGMENTS` (2) instead of
  sending it in three billable parts.
- The queue's messages are written to fit **one** segment, with clinic and room names shortened when they
  are long. `tests/unit/notifications/test_sms_segments.py` renders every one, in every language the
  templates have, with the longest names the database allows.

## 6. Replies: STOP means stop

A patient who replies `STOP` (or `STOPALL`, `UNSUBSCRIBE`, `END`, `QUIT`, `CANCEL`, `OPTOUT`) to any ClinicQ
SMS is opted out of **every** channel at once: SMS, web push and WhatsApp, including a message already
queued or held by quiet hours. `START` (or `UNSTOP`, `SUBSCRIBE`, `OPTIN`) undoes it. Case and spacing do not
matter; any other reply is recorded as ignored and changes nothing. ClinicQ does not text back to confirm,
so a STOP never costs a message.

Replies use the same secret as receipts. In the Africa's Talking dashboard, under SMS callback URLs, set the
incoming messages URL to
`https://<ClinicQ's address>/api/v1/webhooks/sms/africastalking/<the secret>/inbound`. Each reply is
recorded once in `sms_inbound_event` (a repeated callback answers `duplicate`), with the keyword (when it was one) and what it
did, never the text.

A patient can also stop messages, set quiet hours or mute a message from the **Message settings** panel on
their ticket page. Quiet hours hold every message except the three that say "come now" (you are next,
please come in, you were called again); an opt-out stops those too.

---

**Refs:** [Issue 65](../GITHUB/ISSUES/M9/ISSUE_65_sms_gateway_cost_caps.md) ·
[Issue 67](../GITHUB/ISSUES/M9/ISSUE_67_notification_preferences_quiet_hours.md) · `src/modules/notifications/sms.py` ·
`src/modules/notifications/budget.py` · `src/core/webhook_gateways/africastalking.py`
