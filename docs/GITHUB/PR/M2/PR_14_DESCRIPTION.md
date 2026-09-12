# PR: Watch staging and production, track errors with their ids, and write the alerts runbook (Issue 14 / M2-14)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#14](https://github.com/Billykat7/clinicQ/issues/14)

This is the last issue of M2. Until now the team would learn that staging was down when someone
tried to demo it. This PR adds the minimum that changes that:

- an uptime monitor (Gatus, configured in the repository) that posts one alert per incident, naming
  the role that responds, and one message on recovery;
- error tracking that tags every unhandled exception with the request id from Issue 6 and the
  release and commit from Issue 10, so an exception leads to its log lines and its code;
- `/metrics` for request rate, errors and latency;
- a one-page runbook.

With no real staging host or team channel yet, both headline criteria were demonstrated on the
local staging stand-in from Issue 11, running the release this branch published. Stopping the app
raised exactly one alert, after 122 seconds. A deliberate exception reached a self-hosted GlitchTip
with the response's request id, `clinicq@0.0.5-test` and the commit.

## Summary

- **Uptime: `infra/monitoring/gatus.yaml`,** run by `infra/docker/docker-compose.monitoring.yml`. It
  checks `/health` on staging and production every 60 s. Three failures in a row open an incident,
  which sends one Slack (or Discord) message naming the responsible role and its backup and
  pointing to the runbook. Two good checks in a row close it with one message. No repeats.
- **Error tracking: `src/core/telemetry.py`,** with `sentry-sdk[fastapi]` 2.69.1, on when `SENTRY_DSN`
  is set. Each event carries `request_id` (tagged on Sentry's per-request scope as the request
  starts), `release=clinicq@<version>`, `git_sha` and the environment. No cookies, request bodies,
  user, or stack-frame local variables leave the process, and log lines are breadcrumbs, so one
  exception is one event. GlitchTip, which speaks the same protocol, is in the monitoring compose
  file behind the `errors` profile for a self-hosted option.
- **Metrics:** `/metrics`, opt-in (`METRICS_ENABLED`). It serves `clinicq_http_requests_total` by
  method, route *template* and status class, and `clinicq_http_request_duration_seconds`. Outside
  development it requires `Authorization: Bearer <METRICS_TOKEN>`.
- **Proving it:** `ERROR_TRACKING_TEST_ROUTE=true` adds `GET /health/error-tracking-test`, which fails
  on purpose. Production refuses the setting, through Issue 12's single validation path, which also
  gained the "no /metrics without a token outside development" rule.
- **Deploy notifications** (version, environment, result, release notes link) shipped with Issue 11's
  deploy; this issue's criterion is checked against them below.
- **`docs/CICD/RUNBOOK_ALERTS.md`:** who responds (role E, then F after 15 minutes), and what to do for
  each message, on one page. **`infra/monitoring/README.md`:** how it is set up.

## Design notes

**One check per environment, on liveness.** A monitor on `/health` and another on `/health/ready`
would send two alerts when the app stops: one incident, two pages. The issue asks for `/health`,
and a stopped app, a crashed container and a dead host all fail it. A dependency outage shows in
readiness and in the runbook's step 6. Watching dependencies separately, with their own routing,
is Issue 104's.

**Once per incident, by construction.** `failure-threshold: 3` means one slow check never pages
anyone. `send-on-resolved: true` sends the end of an incident as a message. There is no
`repeat-interval`, so an incident that lasts an hour sends two messages, not sixty. The worst case
to alert is 3 × 60 s = 3 minutes, inside the 5-minute target. `test_monitoring_config.py` holds all
of it: the arithmetic, the role in every alert, the runbook link, and webhook and host URLs that
only come from the environment.

**Run the monitor somewhere else.** Gatus in the app's own compose would go down with the host it
is meant to report. It has its own compose file for another machine, and the README says so.

**The request id is tagged when the request starts.** Starlette's outermost error middleware
captures the exception after Issue 6's middleware has unbound the request's logging context, so a
`before_send` hook would find no id. Tagging Sentry's per-request scope at the start of the request
puts the id on anything captured later in that request.

**No frame locals.** `send_default_pii=False` keeps cookies out of the event's request section. The
first test still found the session cookie in the event: Sentry records each stack frame's local
variables, and the ASGI `scope` among them carries every header. In this app, frame locals could
also hold patient data. `include_local_variables=False`; the traceback lines are enough to find a
bug.

**Opt-in `/metrics`.** Enabled by default, with a token required outside development, it would have
made every existing staging and production configuration invalid (18 tests said so). Opt-in keeps
the default app unchanged; an environment that enables it must set the token.

**Out of scope:** dashboards, an on-call rota and a status page (Issue 104); load testing (Issue 105).

## Changes

- **`src/core/telemetry.py`** (new): `init_error_tracking`, `tag_request`, `MetricsMiddleware`,
  `metrics_endpoint`, `raise_for_error_tracking`.
- **`src/main.py`:** error tracking before the app is built, the metrics middleware and route when
  enabled, the test route when enabled. **`src/core/request_logging.py`:** `tag_request` on each
  request.
- **`src/core/config.py`:** `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `METRICS_ENABLED`, `METRICS_TOKEN`,
  `ERROR_TRACKING_TEST_ROUTE`, and two rules in `configuration_problems()`. **`.env.example`:**
  regenerated.
- **`requirements.txt`:** `sentry-sdk[fastapi]==2.69.1` (pip-audit: no known vulnerabilities; the image
  grows 2 MB, to 830 MB).
- **`infra/monitoring/gatus.yaml`**, **`infra/monitoring/README.md`**,
  **`infra/docker/docker-compose.monitoring.yml`** (new; Docker files stay under `infra/docker/`).
- **`tests/integration/platform/test_telemetry.py`** (new): one exception becomes one event with the
  response's request id, the release, the commit and the environment, and no cookie; error tracking
  is off without a DSN; the test route only when asked for; metrics by route template; the token.
  **`tests/unit/platform/test_monitoring_config.py`** (new): the checks, the roles, the timing,
  once per incident, no secrets, and the runbook.
- **`docs/CICD/RUNBOOK_ALERTS.md`** (new); **`docs/CICD/ENVIRONMENTS.md`:** the new settings and rules.

## Testing

The demonstrations ran against the local staging stand-in from Issue 11 (compose project
`btk-clinicq-staging`, linux/amd64 under emulation), serving the release this branch published:
`v0.0.5-test` (run green; 830 MB; `/health` → `0.0.5-test`, `5150045`). It was deployed through
`deploy.sh` with `SENTRY_DSN`, `ERROR_TRACKING_TEST_ROUTE=true` and `METRICS_ENABLED=true` with a
token (pre-flight: `OK`). The error tracker was GlitchTip 6.2.6 from `docker-compose.monitoring.yml`
(`--profile errors`). The team channel was a local webhook stand-in that logs each message with
its arrival time.

- [x] **An exception reaches error tracking with the request id and the release tag:**

      ```text
      GET /health/error-tracking-test → HTTP 500
      response X-Request-ID: 01a091d3-9444-7539-832f-b7687098c35b
      issues in GlitchTip: 1
        1 ErrorTrackingTestError: Deliberate error to prove error tracking end to end (Issue 14) | events: 1
      latest event tags:
        request_id = 01a091d3-9444-7539-832f-b7687098c35b
        release = clinicq@0.0.5-test
        git_sha = 5150045161212cee5ee9e2afa2b059522170264f
        environment = staging
      request_id matches the response header: True
      any cookie in the stored event: False
      ```

      The same id finds the request's log line: `docker logs btk-clinicq-staging-app-1 | grep
      01a091d3-…` → `"Unhandled error on GET /health/error-tracking-test", "request_id":
      "01a091d3-9444-7539-832f-b7687098c35b"`.
- [x] **An intentional staging outage raises one alert within 5 minutes, naming the role:**
      Gatus from `docker-compose.monitoring.yml` with the
      repository's `gatus.yaml`, watching the stand-in and an always-up stand-in for production. After
      two healthy checks each, the staging app container was stopped and left down for 6.5 minutes:

      ```text
      T0         19:00:05Z  docker stop btk-clinicq-staging-app-1 (staging goes down)
      ALERT      19:02:08Z  first message after 122 s
      STILL-DOWN 19:06:39Z  after 393 s: 1 message(s) in the channel
      T1         19:06:39Z  docker start btk-clinicq-staging-app-1 (staging comes back)
      RESOLVED   19:08:07Z  second message 87 s after the restart
      END        19:10:37Z  2 message(s) in the channel in all
      ```

      The alert, as the channel received it (Slack format):

      ```text
      An alert for *clinicq/staging* has been triggered due to having failed 3 time(s) in a row:
      > ClinicQ STAGING is not answering /health. Responsible: DevOps/QA Lead (role E), backup
        Data & Research Lead (role F). Act within the working day: see docs/CICD/RUNBOOK_ALERTS.md,
        section Staging or production is down.
      Condition results: ❌ [STATUS] (0) == 200 · ❌ [BODY].status (INVALID) == ok · ✅ [RESPONSE_TIME] < 5000
      ```

      and at 19:08:07Z: `An alert for *clinicq/staging* has been resolved after passing successfully
      2 time(s) in a row`. The always-up production endpoint sent nothing.
- [x] **Found by running it, and fixed:** Gatus refused the first configuration (`alert description must
      not have " or \`); the guard test now checks for it. The first telemetry test found the session
      cookie leaving in a stack frame's locals (above).
- [x] **The suite:** 1035 passed, 16 skipped, 9 xfailed; `./scripts/ci-local.sh --no-docker` green in 82 s,
      pip-audit "No known vulnerabilities found", coverage 78.1%.

## Acceptance criteria

- [x] An intentional staging outage raises an alert within 5 minutes: 122 s, on the local stand-in
      (no real staging host yet); the configuration guarantees at most 3 minutes.
- [x] An unhandled exception appears in error tracking with request id and release tag (above,
      matching the response's `X-Request-ID`).
- [x] Alerts name a responsible role, not just a URL ("Responsible: DevOps/QA Lead (role E), backup
      Data & Research Lead (role F)"; guarded).
- [x] Deploy notifications include the version and a link to the release notes: shipped by Issue 11,
      shown in PR #126 (`✅ deployed: ClinicQ 0.2.0 (a3be05c) → staging` with the
      `RELEASE_v0_2_0.md` link) and in the `v0.0.4-test` staging run's message.
- [x] No alert fires more than once per incident (deduplicated): one message when the outage began and one when it ended, over an outage of 6.5 minutes (about six more failed checks after the alert), with nothing in between; guarded by `test_one_incident_sends_one_alert_and_one_resolution`.
- [x] The runbook is short enough that a teammate can act on it at 07:30 without help: one page, one
      section per message, commands to copy. *Whether a teammate can* is for the team to try.

## Risk and rollback

With no `SENTRY_DSN` and `METRICS_ENABLED` off (the defaults), the app behaves as before; the only
always-on change is one tag set per request on the Sentry SDK, a no-op while it is off. The monitor
runs outside the app. Rollback is a revert; the monitoring compose file can be stopped on its own.

**What the team still has to do before this protects anyone:** create the team channel's webhook
(`TEAM_WEBHOOK_URL`, for Gatus and for deploys), choose Sentry or GlitchTip and put the DSN in each
environment's `APP_ENV`, and run the monitoring compose file on a machine that is not a deploy
host. None of these can be created from the repository.

Closes #14
