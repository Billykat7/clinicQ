# Issue 197: Shared S3 bucket: put the project slug first in every key

> **In short:** ClinicQ writes its S3 objects under `clinicq/{env}/{type}/…`, so one bucket (`btkplatform`) can hold every sibling project's logs, documents and backups without them mixing.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26) |
| **Owner** | E, DevOps/QA |
| **Area** | Infra / Production |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | No other issue. |
| **Unblocks** | [Issue 103](../M14/ISSUE_103_backups_restore_drill.md) (off-host backups go to the shared bucket), [Issue 104](../M14/ISSUE_104_monitoring_alerting_status.md) (logs in S3). |

## Context

Each project (clinicq, properties, umojanet, maps, …) had its own bucket, and keys started with the
environment: `dev/logs/error/api/…`. The projects now share one bucket, `btkplatform`. Without the
project's name in the key, two projects' logs for the same environment and day land in the same
folder, and each project's log viewer would list and open the other's.

## Starting point

- `src/core/s3_logging.py` builds log keys as `{env}/logs/{log_type}/{api|web}/{YYYY}/{MM}/{DD}/clinicq-….json`.
- `src/core/s3_logs_query.py` and `src/api/v1/routes/admin.py` list and read under `{env}/logs/`; `src/core/health.py` writes a readiness sentinel under the same prefix.
- `PROJECT_SLUG=clinicq` is already set by `scripts/cd/write-prod-env.sh` and the platform compose file, but the app does not read it.

## Scope

- Key layout `{PROJECT_SLUG}/{dev|uat|prod}/{logs|docs|backups|…}/…`, built in one place
- `PROJECT_SLUG` setting (default `clinicq`), refused when it is not one lowercase key segment
- Log writer, readiness sentinel and log viewer move to the new prefix
- The log viewer never lists or reads another project's keys
- A short ops page describing the shared layout and a per-project access policy

## Out of scope

- Creating the bucket, IAM users and lifecycle rules in AWS.
- Moving objects written under the old layout (they are in the old per-project buckets).
- The same change in the sibling repositories.

## Acceptance criteria

- [ ] A log batch is written to `clinicq/{env}/logs/{level}/{path}/{YYYY}/{MM}/{DD}/clinicq-{YYYYMMDD}-{HHMMSS}.json`
- [ ] The readiness sentinel is `clinicq/{env}/logs/_readiness/probe.json`
- [ ] The log viewer lists only under `clinicq/{env}/logs/` and refuses a sibling project's key without calling S3
- [ ] `PROJECT_SLUG` with a capital, a slash, a space or a leading hyphen is refused at load, naming the setting
- [ ] `.env.example` lists `PROJECT_SLUG`, and `docs/OPS/S3_STORAGE.md` describes the layout

## How to verify

1. `TZ=UTC pytest tests/unit/platform/test_s3_logging.py tests/unit/platform/test_s3_logs_query.py tests/integration/admin/test_admin_logs_viewer.py`
2. Write `PROJECT_SLUG=Clinic/Q` to a file and run `python scripts/check_config.py <file>`: it names `PROJECT_SLUG` and exits 1.

## Files touched

- `src/core/config.py`
- `src/core/s3_logging.py`
- `src/core/s3_logs_query.py`
- `src/core/health.py`
- `src/api/v1/routes/admin.py`
- `docs/OPS/S3_STORAGE.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [how to read this spec](../README.md)

Closes #197
