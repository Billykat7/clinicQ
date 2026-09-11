# Issue 4: Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes

> **In short:** The shared vocabulary of the product: the enums, time helpers and error shape every module uses, so six people stop inventing their own.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 1 (weeks 1–2) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Foundation |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 1](../M1/ISSUE_1_repo_scaffold_app_factory.md): Repository scaffold, Python 3.14 + FastAPI app factory, typed settings |
| **Unblocks** | [Issue 8](../M1/ISSUE_8_test_factories_seed_data.md): Test factories and `seed_dev_data.py` demo dataset<br>[Issue 15](../M3/ISSUE_15_staff_user_security_core.md): Staff user model and security core (JWT, refresh rotation, password hashing)<br>[Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification |

## Context

Six developers inventing their own status strings, timestamp handling and error shapes is the
fastest way to an unmergeable codebase. This issue fixes those three decisions once: wire-safe values
are enums, business datetimes are `Africa/Johannesburg`, and every API error uses one envelope.

## Starting point

- `src/commons/enums.py` and `src/commons/exceptions.py` already hold the kernel's enums and error envelope. Add the ClinicQ enums (`SiteSector`, `TicketStatus`, `TicketSource`, `DisplayMode`) there rather than in a new file.
- The kernel has no `now_sast()` helper yet; the app timezone (`APP_TIMEZONE`) is defined in `src/core/s3_logging.py`, with a second copy in `src/web/routes.py`. Put the time helpers next to the other shared code in `src/commons/`, and make that the one definition.
- `StaffRole` overlaps the kernel's `UserRole`; extend that rather than adding a second role enum.

## Scope

- `app/core/enums.py`: `SiteSector`, `TicketStatus`, `TicketSource`, `DisplayMode`, `StaffRole`, `NotificationChannel`
- `app/core/time.py`: `now_sast()`, `to_sast()`, `business_date()`, storing in UTC and presenting in `Africa/Johannesburg`
- One error envelope and exception handlers mapping domain errors to HTTP status codes
- ULID/UUIDv7-style identifiers so ids sort by creation time and are safe in URLs
- A lint rule (or test) that fails on a naive `datetime.now()` or a magic status string

## Out of scope

- Using the enums in models (each domain issue does that).
- The ticket state machine (Issue 41).

## Acceptance criteria

- [ ] Every status value used on the wire is an enum member, not a bare string
- [ ] `now_sast()` returns a timezone-aware datetime in `Africa/Johannesburg`
- [ ] A domain error raised in a service becomes a documented JSON error body with the right status
- [ ] The guard test fails when a naive `datetime.now()` is introduced
- [ ] Identifiers are URL-safe and monotonically sortable
- [ ] Enums are documented in the OpenAPI schema with their allowed values

## How to verify

1. Add `datetime.now()` to any module: the conventions test fails and names the file.
2. `/docs` (OpenAPI) lists each new enum with its allowed values.
3. Raise a domain error from a test route: the response is the documented JSON envelope with the mapped status.

## Files touched

- `src/commons/enums.py`
- `src/commons/exceptions.py`
- `src/commons/time.py`
- `src/commons/ids.py`
- `src/core/error_handlers.py` (the handlers, registered in `src/main.py`)
- `src/api/v1/routes/reference.py` (publishes the enums in OpenAPI)
- `tests/unit/commons/test_conventions.py`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #4
