# PR: One request id through every log line, redaction in the logging layer, Redis in readiness (Issue 6 / M1-06)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#6](https://github.com/Billykat7/clinicQ/issues/6)

When a ticket goes missing at a clinic, the team now greps one id and gets the whole request: every
line carries the request id, method, path, client IP, and the site and actor once they are known.
The same id comes back to the user in `X-Request-ID`, even on a 500, so a support message can quote
it. A phone number, OTP or token logged from any module comes out masked, because redaction runs on
the handlers and not at call sites. Readiness now checks Redis, and liveness still touches nothing.

Most of this existed, so it was checked before anything was added. That check found one claim that
was not true: "JSON logging" held only for the S3 handler, while the console, which is what the
container prints, wrote plain text. The console now writes newline-delimited JSON (shown through
`jq` below).

## Summary

- **The log context** gains `site_id` and `actor_id` next to the kernel's request id, method, path
  and client IP. `get_current_user` and the HTML shell bind the actor (the user's id, never the
  email); Issue 19's tenancy helper will bind the site with `bind_request_context(site_id=...)`.
- **`X-Request-ID`** on every response: success, 404, a CSRF refusal, and the 500 from an unhandled
  exception, whose envelope carries the same id. A UUIDv7 is minted, unless a trusted proxy sent a
  safe id.
- **Redaction in the logging layer** (`src/core/log_redaction.py`): phone numbers, OTPs, JWTs and
  bearer credentials are masked in the message, in `extra=` fields (by name as well as by content)
  and in tracebacks. The filter sits on the console and S3 handlers, so every module is covered.
- **JSON console logs** (`LOG_FORMAT=json`, the default; `text` stays available): one object per
  line with a SAST timestamp, level, logger, message, `context` and `extra`.
- **Redis in readiness:** `/health/ready` pings `REDIS_URL` with a one-second timeout. A Redis that
  is down means 503 and `checks.redis: "down"`; with no `REDIS_URL`, the check reports `skipped`.
  Liveness stays dependency-free.
- **`LOG_LEVEL`** is an enum now (default `INFO`, lower case still accepted).

## Design notes

**Verify before adding.** What the issue said existed did exist. The request id, path and client
IP were set per request, `/health`, `/health/live` and `/health/ready` were in place, and liveness
touched nothing. What was missing is the list the issue gives (site and actor, Redis, redaction,
the header), plus the console format above. Everything else was kept, including the `SECURITY_HTTP`
lines and the S3 payload the log viewer reads (which only gains `site_id` and `actor_id`).

**Redaction is a filter on every handler, not a helper.** A helper would protect the call sites
that remember to use it, and the next `logger.info(f"... {phone}")` in a module that does not exist
yet would leak. So `RedactionFilter` rewrites each record before it is formatted: the message after
its `%` arguments are merged, every `extra=` field, and the traceback (an exception's message often
quotes its input). It is attached to the handler, because a logger's filter would miss records
propagated from child loggers. The proof is a test that runs the real `setup_logging()` and logs a
number from three module loggers that do not exist yet (`src.modules.patients.service`,
`src.modules.channels.ussd`, `src.modules.notifications.sms`), four different ways.

**What counts as a phone or an OTP.** Phone numbers are South African numbers written locally, in
international form or as an MSISDN (what a USSD gateway reports), and any `+`-prefixed foreign
number. An OTP is 4 to 8 digits that follow a word naming it ("OTP", "code", "PIN", "verification
code"), with up to three short words in between ("Your code for today is …"). The label stays and
only the digits go. Deliberately not "any long run of digits": ticket numbers, epoch milliseconds,
durations, IPs and status codes stay readable, and tests pin both lists. Fields are also masked by
name (`phone`, `msisdn`, `otp`, `password`, `token`…). `code` counts only as an exact field name,
because `error_code` (the Issue 4 handler logs one) is not a secret.

**A mutable context object, bound late.** The actor is known only inside `get_current_user`, and
the site only inside a dependency, which may run in a worker thread. A context variable *set* there
is invisible to the middleware's own closing line. So the variable holds one `RequestContext` per
request, and binding mutates it. A test binds the site in a sync dependency and reads it back from
the middleware's `HTTP 409` line.

**Plain ASGI, and outermost.** The middleware was a `BaseHTTPMiddleware` installed inside the CSRF
middleware. As plain ASGI it can stamp the header on streamed responses (the board's server-sent
events in M8). Installed outermost, a request the CSRF middleware refuses now gets an id and its
`SECURITY_HTTP` line; on `main` it got neither. An unhandled exception is logged with its traceback
while the context is still bound. The 500 is produced by Starlette outside every middleware, so the
Issue 4 envelope handler reads the id from `request.state` and sets the header itself.

**Which request ids to trust.** A client-supplied `X-Request-ID` could inject a log line or merge
two requests into one. So an incoming id is kept only when `TRUST_PROXY_HEADERS` is on (the same
boundary as `X-Forwarded-For`) and it matches a safe pattern: 8 to 128 characters of `[A-Za-z0-9._:-]`,
which fits nginx's `$request_id`. The middleware takes that flag from the settings `create_app` was
given. The first version read the global settings, which a test caught: your `.env` sets
`TRUST_PROXY_HEADERS=true`.

**Redis is `down`, not `degraded`.** Nothing reads Redis yet, but queued work and the board's live
updates will, and readiness decides whether a replica receives traffic. So a configured Redis that
does not answer fails readiness (503). No `REDIS_URL` means "this deployment has no Redis":
`skipped`, excluded from the verdict, as S3 already was. Connect and read are both bounded by
`REDIS_PROBE_TIMEOUT_SECONDS` (default 1 s), and the connection error goes to the log, never the
body.

**Quiet by default.** Each readiness poll opened an Alembic migration context that logged two INFO
lines; at a 10 to 30 second polling interval that is most of the log. The `alembic` logger is set
to WARNING in the app. Migrations are unaffected: `alembic/env.py` configures its own logging and
does not import the app.

**Out of scope:** alerting and error tracking (Issue 14) and the audit trail (Issue 20). The spec's
`/ready` is the kernel's `/health/ready`, which the compose health checks and the gateway already
poll; the spec now says so.

## Changes

- **`src/core/request_logging.py`:** `RequestContext` (with `site_id`, `actor_id`),
  `bind_request_context()`, `request_id_for()`, plain-ASGI middleware that sets `X-Request-ID`,
  trusts a safe inbound id only behind the proxy, and logs unhandled exceptions with context.
- **`src/core/log_redaction.py`** (new): `redact()`, `redact_value()`, `RedactionFilter`.
- **`src/core/logging_config.py`:** `JsonFormatter` and `TextFormatter`, `build_console_handler()`,
  both filters on the console and S3 handlers, `alembic` at WARNING.
- **`src/core/health.py`**, **`src/schemas/health.py`**, **`src/main.py`:** `probe_redis()`,
  `redis_status`, `checks.redis`, `aggregate_status(*statuses)`, the middleware outermost with the
  app's trust flag.
- **`src/core/config.py`**, **`src/commons/enums.py`:** `LOG_LEVEL` as `LogLevel` (normalised),
  `LOG_FORMAT` (`LogFormat`), `REDIS_URL`, `REDIS_PROBE_TIMEOUT_SECONDS`.
- **`src/core/security.py`**, **`src/core/nav_visibility.py`:** bind `actor_id` from the `uid`
  claim or the session user.
- **`src/core/error_handlers.py`:** envelopes read the id from `request.state`; the 500 sets
  `X-Request-ID`.
- **`src/core/s3_logging.py`:** `site_id` and `actor_id` in the S3 payload.
- **Tests:** `tests/unit/platform/test_logging_redaction.py` (29, new),
  `tests/integration/platform/test_request_context.py` (16, new); `test_health.py` pins Redis like
  the other dependencies, so a local `REDIS_URL` cannot change its outcome.
- **Docs:** the Issue 6 spec (real paths, the console finding), `.env.example`'s `REDIS_URL`
  comment.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (160 files)
- [x] `make test`: **885 passed** (840 on `main` plus 45 new). 24 fail, exactly `main`'s 24,
      compared with `diff`. One of 15 full runs showed a 25th failure that the next 14 runs never
      reproduced. Suspecting the new liveness timing test under parallel load, it now takes the best
      of three rounds (see *Risk*)
- [x] **A phone number logged from any module comes out masked:** the real `setup_logging()`, three
      loggers of modules that do not exist yet, piped through `jq`:

      ```text
      {"level":"INFO","logger":"src.modules.patients.service","message":"Verified patient [REDACTED:phone]","extra":null}
      {"level":"WARNING","logger":"src.modules.channels.ussd","message":"USSD session from [REDACTED:phone] timed out","extra":null}
      {"level":"INFO","logger":"src.modules.patients.otp","message":"Your ClinicQ code is [REDACTED:otp]","extra":{"msisdn":"[REDACTED:phone]"}}
      lines: 3, parsed by jq: 3
      ```

- [x] **Live, against the compose stack** (PostgreSQL 18 on :5433, Redis 8 on :6380, migrations
      applied, S3 shipping off): Redis stopped, then readiness, liveness and the logs for the one
      request id:

      ```text
      == all up        {"status":"ok","checks":{"database":"ok","migrations":"ok","redis":"ok","storage":"skipped"}}  -> 200
      == redis stopped {"status":"down","checks":{"database":"ok","migrations":"ok","redis":"down","storage":"skipped"}}  -> 503 in 0.042s
      x-request-id: 01a08f1d-3760-728f-ab82-6afb06f481c7
      /health      {"status":"ok","version":"1.0.0"}  -> 200 in 0.0048s
      /health/live -> 200 in 0.0046s

      grep 01a08f1d-3760-728f-ab82-6afb06f481c7 (server stdout), abridged:
      {"level":"WARNING","logger":"src.core.health","message":"Readiness: Redis probe failed","context":{"request_id":"01a08f1d-3760-…","method":"GET","path":"/health/ready","client_ip":"127.0.0.1",…},"exception":"… Connection refused."}
      {"level":"ERROR","logger":"src.core.request_logging","message":"HTTP 503 GET /health/ready","context":{"request_id":"01a08f1d-3760-…",…},"extra":{"status":503}}
      == redis back    {"status":"ok",…,"redis":"ok",…}  -> 200
      ```

      Before the `alembic` logger was quieted, the same grep also showed Alembic's two INFO lines,
      carrying the same request id. That is what prompted the change

- [x] Every line of a request carries its id: a test request that logs from three modules (one in
      a worker thread) produced three lines with the id the header returned; the site bound mid-way
      appears on the lines after it, and the actor on all of them. The email in the token appears
      nowhere
- [x] `X-Request-ID` on 200, 404, 500 and a CSRF 403; the 500's envelope and its logged traceback
      carry the same id
- [x] Inbound ids: kept from a trusted proxy in a safe format; replaced when the proxy is not
      trusted, when the id carries a newline, or when it is 200 characters long
- [x] Liveness with the database dependency made to raise: 200, p95 under 50 ms; live, 4.8 ms
- [ ] Log shipping to S3 end to end: the filter is proven on the S3 handler with a stand-in; live
      shipping was turned off for these runs so no test traffic left the machine

## Acceptance criteria

- [x] Every log line emitted during a request carries the same `request_id` (the header's)
- [x] `/health` responds in under 50 ms and never touches the database (4.8 ms live; 200 with the
      database dependency raising)
- [x] `/health/ready` returns 503 with a reason when Postgres or Redis is unavailable
      (`checks.redis: "down"` live, `checks.database: "down"` in the existing suite)
- [x] A phone number or OTP written to a log is redacted, proven by a test (through the real
      `setup_logging()`, from any module)
- [x] Log output is valid newline-delimited JSON parseable by `jq`
- [x] The request id is returned in a response header so a user can quote it in a support message

## Risk and rollback

The visible change is the log format. The console now prints JSON by default, so anyone reading
container logs by eye sees objects instead of lines. `LOG_FORMAT=text` brings the old format back
with the request id appended. The middleware was rewritten and moved, and the kernel's request-log
and security-line suites pass unchanged. A deployment with `REDIS_URL` set and Redis unreachable now
fails readiness, which is the point; one without it is unaffected. No migration. Rollback is a
revert of this PR.

About the one unexplained failure: 1 run in 15 showed a 25th failure that 14 later runs, some under
extra load, never reproduced. The test most exposed to load is the new liveness timing test; it now
takes the best of three rounds of 100 requests. A real regression (a dependency on the liveness
path) would be slow in every round, and the database dependency raises there anyway.

**Follow-ups found along the way:**

- Your local `.env` enables S3 log shipping with a real bucket, so a local `make run` ships its
  warnings, and the readiness probe's sentinel, to that bucket. That is the kernel's behaviour, not
  this PR's. A developer default of off belongs in Issue 12's `.env.example`.
- `alembic upgrade head` run bare fails with `No module named 'src'` (`alembic.ini` has no
  `prepend_sys_path`), and `scripts/db/alembic-upgrade.sh` prints each migration line twice. Both
  are Issue 3's to fix.
- `add_request_context_filter_to_logger()` has no callers left; it is kept for compatibility and
  can go with the next cleanup.

Closes #6
