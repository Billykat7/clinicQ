# Monitoring

Issue 14's baseline. What to do when an alert arrives is in
[`docs/CICD/RUNBOOK_ALERTS.md`](../../docs/CICD/RUNBOOK_ALERTS.md); this page is how it is set up.

| What | How | Where it reports |
|------|-----|------------------|
| Uptime | Gatus checks `/health` on staging and production every minute ([`gatus.yaml`](gatus.yaml)) | one message per incident and one on recovery, to the team channel, naming the responsible role |
| Errors | `sentry-sdk` in the app (`src/core/telemetry.py`) sends unhandled exceptions to `SENTRY_DSN` | Sentry (hosted free tier) or GlitchTip (self-hosted, below), each event tagged `request_id`, `release`, `git_sha` |
| Waiting-room boards | ClinicQ's own watch (Issue 61): a paired screen silent for `DISPLAY_DEVICE_SILENT_MINUTES` (10) | one message per silence and one when it is back, to the same `TEAM_WEBHOOK_URL`, naming the screen and its clinic ([`docs/OPS/KIOSK_SETUP.md`](../../docs/OPS/KIOSK_SETUP.md)) |
| Deploys | `deploy.yml` posts every deploy's result (`scripts/cd/notify_deploy.py`) | the team channel: environment, version, commit, result, release notes |
| Metrics | `/metrics` (Prometheus text) with `METRICS_ENABLED=true`, behind `METRICS_TOKEN` | request rate, errors by status class and latency, by route template; `clinicq_live_streams_open`, the dashboards and waiting-room boards connected to the instance (Issue 57), which should rise and fall with the clinics' opening hours and never climb through a day |

## Running it

Gatus has to run somewhere other than the hosts it watches: a monitor on the host that failed
fails with it. A small separate VM, or a teammate's always-on machine for now.

```bash
export STAGING_URL=https://staging.… PRODUCTION_URL=https://… TEAM_WEBHOOK_URL=https://hooks.slack.com/services/…
docker compose -f infra/docker/docker-compose.monitoring.yml --project-directory infra/docker up -d
open http://localhost:8080        # the status page, local to that machine
```

## Error tracking: Sentry or GlitchTip

Either works with no code change: the app only needs a DSN.

- **Sentry, hosted:** create a project (platform: Python/FastAPI), and put its DSN in each
  environment's `SENTRY_DSN` (in the `APP_ENV` secret). The free tier is enough for a pilot.
- **GlitchTip, self-hosted,** on the monitoring machine:

  ```bash
  GLITCHTIP_SECRET_KEY=$(openssl rand -hex 32) GLITCHTIP_DOMAIN=https://errors.… \
    docker compose -f infra/docker/docker-compose.monitoring.yml --project-directory infra/docker --profile errors up -d
  ```

  Then sign in, create an organisation and a project, and copy its DSN.

On staging only, `ERROR_TRACKING_TEST_ROUTE=true` adds `GET /health/error-tracking-test`, which
fails on purpose: request it, take the `X-Request-ID` from the response, and find the event with that
`request_id`. Production refuses the setting.
