# Issue 4: Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes

**Area:** Backend / Foundation
**Milestone:** M1 - Foundation & Local CI
**Owner role:** Backend Lead
**Depends on:** Issue 1
**Estimate:** 1 day
**Status:** Planned

## Context

Six developers inventing their own status strings, timestamp handling and error shapes is the
fastest way to an unmergeable codebase. This issue fixes those three decisions once: wire-safe values
are enums, business datetimes are `Africa/Johannesburg`, and every API error uses one envelope.

## Scope

- `app/core/enums.py`: `SiteSector`, `TicketStatus`, `TicketSource`, `DisplayMode`, `StaffRole`, `NotificationChannel`
- `app/core/time.py`: `now_sast()`, `to_sast()`, `business_date()`, storing in UTC and presenting in `Africa/Johannesburg`
- One error envelope and exception handlers mapping domain errors to HTTP status codes
- ULID/UUIDv7-style identifiers so ids sort by creation time and are safe in URLs
- A lint rule (or test) that fails on a naive `datetime.now()` or a magic status string

## Acceptance criteria

- [ ] Every status value used on the wire is an enum member, not a bare string
- [ ] `now_sast()` returns a timezone-aware datetime in `Africa/Johannesburg`
- [ ] A domain error raised in a service becomes a documented JSON error body with the right status
- [ ] The guard test fails when a naive `datetime.now()` is introduced
- [ ] Identifiers are URL-safe and monotonically sortable
- [ ] Enums are documented in the OpenAPI schema with their allowed values

## Files touched

- `app/core/enums.py`
- `app/core/time.py`
- `app/core/errors.py`
- `app/core/ids.py`
- `tests/unit/test_conventions.py`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #4
