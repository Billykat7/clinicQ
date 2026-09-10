# Issue 6: Structured logging, request-context middleware, health & readiness probes

> **In short:** When something breaks at a clinic, one request id ties together every log line about it, and the health probes say which dependency is down.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 1 (weeks 1–2) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Backend / Observability |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 1](../M1/ISSUE_1_repo_scaffold_app_factory.md): Repository scaffold, Python 3.14 + FastAPI app factory, typed settings |
| **Unblocks** | No other issue waits on this one. |

## Context

When a ticket goes missing during a pilot, the team needs to reconstruct what happened from logs
rather than from memory. Structured logs with a request id, the acting user and the site id make that
possible, and the health probes are what the deploy in M2 and the monitoring in M14 depend on.

## Starting point

- `src/core/logging_config.py` (JSON logging), `src/core/request_logging.py` (request id, path, client IP) and `src/core/health.py` (`/health`, `/health/live`, `/health/ready`) already exist.
- What is missing: `site_id` and `actor_id` in the log context, a Redis check in readiness, redaction of phone numbers and OTPs, and returning the request id in a response header.

## Scope

- JSON structured logging with level, timestamp, logger, message and a context dict
- Request-context middleware injecting `request_id`, `site_id`, `actor_id` and path into every log line
- `/health` (liveness, no dependencies) and `/ready` (checks database and Redis) endpoints
- Log redaction for phone numbers, OTPs and tokens
- Configurable log level per environment, defaulting to INFO in production

## Out of scope

- Alerting and error tracking (Issue 14).
- The audit trail, which records changes to records rather than requests (Issue 20).

## Acceptance criteria

- [ ] Every log line emitted during a request carries the same `request_id`
- [ ] `/health` responds in under 50 ms and never touches the database
- [ ] `/ready` returns 503 with a reason when Postgres or Redis is unavailable
- [ ] A phone number or OTP written to a log is redacted, proven by a test
- [ ] Log output is valid newline-delimited JSON parseable by `jq`
- [ ] The request id is returned in a response header so a user can quote it in a support message

## How to verify

1. Hit any page, then grep the logs for its request id: every line for that request carries it, and so does the response header.
2. Stop Redis: `/health/ready` returns 503 naming Redis, while `/health` stays 200.
3. Log a phone number from a test: the output shows it redacted.

## Files touched

- `src/core/logging_config.py`
- `src/core/request_logging.py`
- `src/core/health.py`
- `tests/unit/platform/test_logging_redaction.py`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #6
