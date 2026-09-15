# PR: Shared S3 bucket: put the project slug first in every key (Issue 197 / M14-197)

**Milestone:** [Milestone 14: Production Readiness, Pilot & Go-live](https://github.com/Billykat7/clinicQ/milestone/14) ·
**Issue:** [#197](https://github.com/Billykat7/clinicQ/issues/197) · **Builds on:** #7 and #8 (S3 log
writer and viewer) · **Unblocks:** #103 (off-host backups), #104 (logs in S3)

The sibling projects (clinicq, properties, umojanet, maps, …) now share one S3 bucket, `btkplatform`, instead
of a bucket each. Until now clinicQ's keys started with the environment (`dev/logs/…`), so two projects' logs
for the same environment and day would land in the same folder. With this PR every clinicQ key starts with
the project's own name:

```
{bucket}/{PROJECT_SLUG}/{dev|uat|prod}/{logs|docs|backups|...}/...
btkplatform/clinicq/dev/logs/warning/api/2026/09/15/clinicq-20260915-113026.json
```

- **`PROJECT_SLUG`** (default `clinicq`) is now a setting. The deploy scripts already set it; the app did not
  read it. A slug with a capital, a slash, a space or a leading hyphen is refused at load, naming the
  setting, because it would put objects where no reader looks.
- **One place builds the prefix:** `Settings.s3_prefix(kind)`, so `s3_prefix("logs")` in development is
  `clinicq/dev/logs/`.
- **The log viewer stays inside clinicQ's prefix.** It lists only `clinicq/{env}/logs/`, and a request for
  another project's key is refused before S3 is called.

## Summary

- **Setting:** `project_slug` in `src/core/config.py` (`PROJECT_SLUG`) with its validator, and
  `Settings.s3_prefix(kind)`. `.env.example` regenerated.
- **Writer:** `src/core/s3_logging.py` writes `{slug}/{env}/logs/{level}/{path}/{YYYY}/{MM}/{DD}/{slug}-….json`.
- **Readiness sentinel:** `src/core/health.py` writes `clinicq/{env}/logs/_readiness/probe.json`.
- **Viewer:** `src/core/s3_logs_query.py` parses the project segment, matches on project and environment,
  lists under the project prefix and refuses keys outside it. `src/api/v1/routes/admin.py` reports the new
  `prefix_used` and describes the layout in the OpenAPI text.
- **Docs:** `docs/OPS/S3_STORAGE.md` (layout, what clinicQ writes, a per-project IAM policy);
  `scripts/db/backup.sh`'s example destination is `s3://btkplatform/clinicq/prod/backups/`.
- **Issue docs:** the spec for #197 in `docs/GITHUB/ISSUES/M14/`, the M14 and issue tables, the counts
  (110 tracked issues) and the progress bars as they read once this merges
  (`make milestone-progress ARGS='--assume-closed 197'`).

## Design notes

- **The slug goes first, not after the environment.** `{slug}/{env}/…` lets one IAM policy per project
  (`btkplatform/clinicq/*`) and one lifecycle rule per project cover every environment and every kind of
  object.
- **`s3_prefix(kind)` takes a plain string.** Only `logs` is written today; documents and backups use the
  same layout when they arrive, without a new enum for one member.
- **Old keys are not read.** Objects written before this (`dev/logs/…`) are in the old per-project buckets
  (`btkcapetour`, `btkhomes`), not in `btkplatform`, so there is nothing to migrate or fall back to.

## Changes

- `src/core/config.py`: `project_slug`, `project_slug_is_one_key_segment`, `s3_prefix`.
- `src/core/s3_logging.py`, `src/core/health.py`, `src/core/s3_logs_query.py`, `src/api/v1/routes/admin.py`,
  `src/commons/enums.py` (docstrings).
- `tests/unit/platform/test_s3_logging.py`, `tests/unit/platform/test_s3_logs_query.py`,
  `tests/integration/admin/test_admin_logs_viewer.py`.
- `.env.example`, `docs/OPS/S3_STORAGE.md`, `scripts/db/backup.sh`, issue and milestone docs.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (302 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with
  `AWS_S3_LOGGING_ENABLED=false`: **2402 passed, 1 skipped, 9 xfailed, 0 failed**.
- [x] The S3 tests, **56 passed**, including the new ones:
  - `test_s3_key_starts_with_the_project_slug`: `PROJECT_SLUG=umojanet` writes
    `umojanet/dev/logs/error/api/2026/08/03/umojanet-20260803-090507.json`;
  - `test_project_slug_must_be_one_lowercase_key_segment`: `ClinicQ`, `clinicq/dev`, empty, `-clinicq` and
    `clinic q` are refused, naming `PROJECT_SLUG`;
  - `test_s3_prefix_is_slug_env_kind`: `clinicq/dev/logs/`, `maps/dev/docs/`;
  - `test_list_prefixes_with_the_project_slug`: the listing asks S3 for `maps/prod/logs/error/`;
  - `test_key_matches_rejects_a_sibling_projects_key` and
    `test_get_s3_log_object_text_refuses_a_sibling_projects_log`: a `properties/…` or `umojanet/…` key with
    the same environment and date never matches, and is never fetched;
  - `test_parse_s3_log_object_key_rejects_the_old_unprefixed_layout`.
- [x] **A round trip through the real code**, on an in-memory bucket named `btkplatform` that already holds a
  umojanet log for the same day: a `logger.warning` through `S3LogHandler`, the readiness probe, then the
  viewer's listing and reads.

  Before (`main`): clinicQ's objects sit at the root of the shared bucket, beside every other project's
  environments:

  ```
  btkplatform/dev/logs/_readiness/probe.json
  btkplatform/dev/logs/warning/api/2026/09/15/clinicq-20260915-113006.json
  btkplatform/umojanet/dev/logs/warning/api/2026/09/15/umojanet-20260915-080000.json
  ```

  After (this branch):

  ```
  bucket objects:
    btkplatform/clinicq/dev/logs/_readiness/probe.json
    btkplatform/clinicq/dev/logs/warning/api/2026/09/15/clinicq-20260915-113026.json
    btkplatform/umojanet/dev/logs/warning/api/2026/09/15/umojanet-20260915-080000.json
  viewer lists: ['clinicq/dev/logs/warning/api/2026/09/15/clinicq-20260915-113026.json'] error: None
  viewer reads clinicq key: {"timestamp": "2026-09-15T11:30:26.484924+02:00", "level": "WARNING", "log_type" ...
  viewer reads umojanet key: None | Invalid log object key. | GetObject calls: 1
  ```

  The one `GetObject` is clinicQ's own object; the umojanet key was refused without a call.
- [x] **`scripts/check_config.py`** on a file holding `PROJECT_SLUG=Clinic/Q`:

  ```
  ✗ PROJECT_SLUG: Value error, PROJECT_SLUG must be lowercase letters, digits and hyphens, e.g. 'clinicq'
  1 problem(s), 1 warning(s): the app would refuse to start with this file.
  ```

  and with `PROJECT_SLUG=clinicq`: `OK: the app would start with this file`.
- [ ] **Not tested against real S3.** The `btkplatform` bucket does not exist yet: listing it with the
  development credentials answers `NoSuchBucket`. The first real upload waits for the bucket.

## Acceptance criteria

- [x] **A log batch is written to `clinicq/{env}/logs/{level}/{path}/{YYYY}/{MM}/{DD}/clinicq-{YYYYMMDD}-{HHMMSS}.json`:**
  the round trip above and `test_s3_key_uses_env_log_type_path_and_date_layout`.
- [x] **The readiness sentinel is `clinicq/{env}/logs/_readiness/probe.json`:** the round trip above.
- [x] **The log viewer lists only under `clinicq/{env}/logs/` and refuses a sibling project's key without calling S3:**
  the round trip and the sibling tests above.
- [x] **`PROJECT_SLUG` with a capital, a slash, a space or a leading hyphen is refused at load, naming the setting:**
  the parametrised test and `check_config.py` above.
- [x] **`.env.example` lists `PROJECT_SLUG`, and `docs/OPS/S3_STORAGE.md` describes the layout.**

## Risk and rollback

- **Create the bucket before turning S3 logging on.** The app does not create it (`AWS_S3_CREATE_BUCKET_IF_MISSING`
  in a local `.env` is not a setting clinicQ reads). Until it exists, uploads fail quietly and readiness
  reports storage as degraded, as with any unreachable bucket.
- **The log viewer shows nothing older than the deploy** for an environment that keeps its old bucket,
  because old keys have no slug. No deployed environment ships logs to `btkplatform` yet.
- **No migration.** Rollback is a revert; keys go back to `{env}/logs/…`.

Closes #197
