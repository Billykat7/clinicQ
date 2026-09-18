# Runbook: alerts

**Who responds:** the **DevOps/QA Lead (role E)**. If E has not answered in the channel within 15
minutes, the backup, the **Data & Research Lead (role F)**, takes it. Say "on it" in the channel
first, so two people do not both act.

Every alert comes once when it starts and once when it ends. Nobody needs to silence anything.

## Staging or production is down

*"ClinicQ PRODUCTION (or STAGING) is not answering /health"*, from the uptime monitor after three
failed checks a minute apart.

1. **Look.** Open `<URL>/health`. If it answers `{"status":"ok"…}` now, it was a blip: wait for the
   "resolved" message and stop here.
2. **What changed?** The channel shows the last deploy. If one ran in the last hour, roll it back
   (step 5) before you investigate anything.
3. **On the host:** `cd "$DEPLOY_DIR" && export DEPLOY_ENV=production` (staging: that
   environment's own `DEPLOY_DIR`, `DEPLOY_ENV=staging`), then `./deploy.sh status`. It shows what
   should be serving. `DEPLOY_DIR` is the variable on the environment's GitHub Environment
   (**Settings → Environments**); the last deploy's run log names the directory it used.
4. **Container stopped or crashing?**
   `docker compose -f docker-compose.prod.yml --project-directory . -p btk-clinicq ps`, then `… logs --tail 100 app`.
   To start it again as it was: `IMAGE=$(cat .deploy/current) docker compose -f docker-compose.prod.yml --project-directory . -p btk-clinicq up -d app`.
5. **Roll back:** `./deploy.sh rollback` (about 10 s; [RUNBOOK_DEPLOY.md](RUNBOOK_DEPLOY.md)).
6. **App up, but `/health/ready` is 503?** The body names the dependency (database, migrations,
   Redis). Fix that; the app recovers on its own.
7. **Write in the channel** what you saw and did. The monitor posts "resolved" by itself after two
   good checks.

## A new error in error tracking

Each event carries `request_id`, `release` (for example `clinicq@0.2.0`) and `git_sha`.

- **The request's logs:** search the logs for the `request_id` (every log line of that request has
  it; `docker compose … logs app | grep <request_id>`).
- **The code:** `git show <git_sha>`, and the release notes of that `release`.
- **Started with a deploy, and hurting users?** Roll back (above), then fix forward.

## A deploy failed

*"❌ deploy FAILED (the previous version keeps serving)"*: nothing to restore; the old version is
still up. Read the run's failed step and [RUNBOOK_DEPLOY.md](RUNBOOK_DEPLOY.md), "When a deploy fails".

## A waiting-room board is silent

*"📺 Waiting-room board silent: “TV by reception” at Zola Clinic has not been heard from since 09:12
(10 min)…"*, from ClinicQ itself (Issue 61). A paired screen reports every minute, so ten minutes without a
report means its screen is probably dark. The message comes once per silence, and *"✅ Waiting-room board
back: …"* follows when it reports again.

1. **Is it one screen, or all of them?** Open `<URL>/admin/display-devices/silent`. Several clinics at once
   points at ClinicQ itself (see *Staging or production is down*) or a provider outage, not at the boxes.
2. **One clinic:** phone the clinic and ask:
   - Is the TV on and showing something?
   - Is the clinic's internet working (can reception use the dashboard)?
   - Was there a power cut? The box comes back by itself within a few minutes of power returning.
3. **The TV shows a pairing code:** the screen was removed or lost its pairing. The clinic manager pairs it
   again under **Clinic settings → Display boards** ([KIOSK_SETUP.md](../OPS/KIOSK_SETUP.md), Part B).
4. **The TV is black or shows a desktop:** walk the clinic through
   [KIOSK_SETUP.md](../OPS/KIOSK_SETUP.md), *When it does not work*.
5. **Write in the channel** what you found. No need to silence anything: the "back" message ends it.

## An SMS cap was reached

*"💸 SMS cap reached: Zola Clinic has sent its 300 SMS for 2026-09-15…"* or *"💸 SMS cap reached for one
patient (…)"*, from ClinicQ itself (Issue 65). It comes once per clinic, or once per patient, per day.
Every SMS refused after it is on the delivery ledger as `suppressed` with the reason. Web push still goes
out.

1. **One clinic, late in a busy day:** the cap is doing its job. Ask the clinic manager whether they want a
   higher cap for that clinic, and set it with `PUT /api/v1/sites/{site_id}/sms-budget`. Today's
   count and spend are at `GET /api/v1/sites/{site_id}/sms-budget`.
2. **Early in the day, or one patient:** suspect a loop. Look at the ledger for that clinic or patient
   (`GET /api/v1/notifications?channel=sms`). If one ticket is producing message after message, turn the
   kill switch on (below) and report it as a bug.
3. **Several clinics at once:** turn the kill switch on first, then investigate
   ([SMS_GATEWAY.md](../OPS/SMS_GATEWAY.md)).
4. **Write in the channel** what you found and whether you changed a cap.

## The SMS kill switch was flipped

*"🛑 SMS kill switch ON: no SMS will be sent (by ops@…: reason)"* and later *"✅ SMS kill switch OFF…"*,
from ClinicQ itself. Someone stopped every SMS on the platform, sign-in codes included.

1. **Was it you, or agreed?** The message names who and why. If nobody in the channel expected it, ask
   that person before turning it off.
2. **While it is on,** patients get web push only, and patients signing in by phone get no code. Keep it on
   no longer than needed: [SMS_GATEWAY.md](../OPS/SMS_GATEWAY.md) §4 says how to turn it off.

## Notifications are failing

*"📉 Notifications failing: 10 of 30 sms messages dead-lettered in the last 60 minutes (33%, the threshold is
25%)…"*, from ClinicQ itself (Issue 71). One transport's messages are dying: at least
`NOTIFICATION_FAILURE_ALERT_MIN_ATTEMPTS` (20) of them reached an outcome in the window
(`NOTIFICATION_FAILURE_ALERT_WINDOW_MINUTES`, 60) and at least `NOTIFICATION_FAILURE_ALERT_RATE` (25%) were
dead-lettered. It comes once per transport per window while the failures go on. Patients may not be hearing
that they are next.

1. **Look at the panel.** `/admin/notifications` shows delivery by transport; filter the list below it by that
   channel and status `dead` and read the last error on a few rows.
2. **Every row says the same thing** (`gateway unreachable`, `gateway answered HTTP 5xx`, a push service's
   `5xx`): the provider is down or refusing us. Check its status page. For SMS, messages go on retrying with
   backoff; if the provider is charging for failures, turn the SMS kill switch on (below). Web push falls back
   to SMS by itself.
3. **The errors name numbers or subscriptions** (`gateway refused the number`, a push `410`): addresses
   are bad, not the provider. Those are dead-lettered at once and not retried; look for a bad batch of numbers
   (an import, a form change) rather than an outage.
4. **`unexpected error: …`**: a bug while preparing messages. Report it with a row id; the rows retry with
   backoff and are dead-lettered at their budget, and nothing else in the sweep is held up.
5. **Write in the channel** what you found. The alert stops by itself once the failure rate falls.

## Not alerts

- *"⏸ not deployed: this environment is not provisioned yet"*: expected until its host exists.
- *"✅ deployed"*: information. Glance at the version.
