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
3. **On the host:** `cd /opt/btk/clinicq && export DEPLOY_ENV=production` (staging:
   `/opt/btk/clinicq-staging`, `DEPLOY_ENV=staging`), then `./deploy.sh status`. It shows what should
   be serving.
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

## Not alerts

- *"⏸ not deployed: this environment is not provisioned yet"*: expected until its host exists.
- *"✅ deployed"*: information. Glance at the version.
