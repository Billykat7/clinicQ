# Issue 6: Structured logging, request-context middleware, health & readiness probes

**Area:** Backend / Observability
**Milestone:** M1 - Foundation & Local CI
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 1
**Estimate:** 1 day
**Status:** Planned

## Context

When a ticket goes missing during a pilot, the team needs to reconstruct what happened from logs
rather than from memory. Structured logs with a request id, the acting user and the site id make that
possible, and the health probes are what the deploy in M2 and the monitoring in M14 depend on.

## Scope

- JSON structured logging with level, timestamp, logger, message and a context dict
- Request-context middleware injecting `request_id`, `site_id`, `actor_id` and path into every log line
- `/health` (liveness, no dependencies) and `/ready` (checks database and Redis) endpoints
- Log redaction for phone numbers, OTPs and tokens
- Configurable log level per environment, defaulting to INFO in production

## Acceptance criteria

- [ ] Every log line emitted during a request carries the same `request_id`
- [ ] `/health` responds in under 50 ms and never touches the database
- [ ] `/ready` returns 503 with a reason when Postgres or Redis is unavailable
- [ ] A phone number or OTP written to a log is redacted, proven by a test
- [ ] Log output is valid newline-delimited JSON parseable by `jq`
- [ ] The request id is returned in a response header so a user can quote it in a support message

## Files touched

- `app/core/logging.py`
- `app/middleware/request_context.py`
- `app/api/health.py`
- `tests/unit/test_logging_redaction.py`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #6
