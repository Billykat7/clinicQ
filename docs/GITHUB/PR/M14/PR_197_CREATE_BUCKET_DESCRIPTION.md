# PR: Create the shared bucket when `AWS_S3_CREATE_BUCKET_IF_MISSING` is on (Issue 197 follow-up / M14-197)

**Milestone:** [Milestone 14: Production Readiness, Pilot & Go-live](https://github.com/Billykat7/clinicQ/milestone/14) ·
**Issue:** [#197](https://github.com/Billykat7/clinicQ/issues/197), closed by PR #198 · **Follows:** PR #198's
risk note "Create the bucket before turning S3 logging on"

PR #198 moved every clinicQ key under `clinicq/{env}/…` in the shared `btkplatform` bucket, but that bucket did
not exist, and `AWS_S3_CREATE_BUCKET_IF_MISSING`, set in the local `.env`, was not a setting the app read.
With this PR it is:

- **`AWS_S3_CREATE_BUCKET_IF_MISSING=true`** creates `AWS_S3_BUCKET` in `AWS_S3_REGION` before the app's first
  write: the log handler's first upload, or the readiness probe. It is checked once per bucket per process.
- **Only a 404 creates.** `HeadBucket` answering 403 means the name exists (another account's, or unreadable
  with these credentials), so nothing is created over it and a warning names the code.
- **It never raises.** A refused `CreateBucket` is a warning, the write that follows fails the usual non-fatal
  way, and the next write checks again. `BucketAlreadyOwnedByYou` (another process won the race) counts as
  created.
- **Off by default.**

**`btkplatform` now exists**, created this way in `af-south-1` (Cape Town). The local `.env` said
`us-east-1`; the region was agreed as `af-south-1` before creating it, because a bucket's region cannot change
later, the other projects already use `af-south-1`, and the data is South African. The local `.env`'s
`AWS_S3_REGION` and `AWS_S3_BASE_URL` now say `af-south-1` (not committed).

## Changes

- `src/core/config.py`: `aws_s3_create_bucket_if_missing` (`AWS_S3_CREATE_BUCKET_IF_MISSING`, default
  `false`); `.env.example` regenerated.
- `src/core/s3_logging.py`: `ensure_bucket_exists(client, bucket, region)`, called by `S3LogHandler` when it
  builds its client. `us-east-1` gets no `LocationConstraint`; every other region does.
- `src/core/health.py`: the readiness probe calls it before writing its sentinel, so `/health/ready` turns OK
  on a fresh deployment instead of degraded.
- `docs/OPS/S3_STORAGE.md`: a "Creating the bucket" section, and the region warning.
- `docs/GITHUB/ISSUES/M14/ISSUE_197_shared_s3_bucket_keys.md`: bucket creation moves from out of scope into
  scope, with its criterion.
- Tests in `tests/unit/platform/test_s3_logging.py` and `tests/unit/platform/test_health_probes.py`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (302 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with
  `AWS_S3_LOGGING_ENABLED=false`: **2412 passed, 1 skipped, 9 xfailed, 0 failed** (PR #198's run was 2402;
  the 10 are the tests below).
- [x] **Bucket creation**, 10 tests:
  - the setting is off unless `AWS_S3_CREATE_BUCKET_IF_MISSING=true`, and while off nothing is checked;
  - `HeadBucket` 404 creates it: no `CreateBucketConfiguration` in `us-east-1`, `LocationConstraint` in
    `af-south-1`;
  - an existing bucket is checked once per process and left alone; a 403 is never created over;
  - a refused `CreateBucket` is logged, never raised, and checked again on the next write;
    `BucketAlreadyOwnedByYou` counts as created;
  - the log handler calls `head_bucket`, `create_bucket`, `put_object` in that order;
  - the readiness probe creates the bucket, answers `ok` and writes `clinicq/dev/logs/_readiness/probe.json`.
- [x] **Against real S3**, with the development credentials, `AWS_S3_CREATE_BUCKET_IF_MISSING=true` and
  `AWS_S3_REGION=af-south-1`. The bucket did not exist; `ensure_bucket_exists` created it and the readiness
  probe wrote its sentinel:

  ```
  bucket: btkplatform | region: af-south-1 | create_if_missing: True | prefix: clinicq/dev/logs/
  before: HeadBucket 404
  WARNING src.core.s3_logging: S3 bucket btkplatform did not exist: created it in af-south-1
  after: HeadBucket OK
  location: af-south-1
  readiness probe: ok
  objects: ['clinicq/dev/logs/_readiness/probe.json']
  public access blocked: True
  default encryption: AES256
  ```

  Then a `logger.warning` through `S3LogHandler`, found by the viewer with the keyword "shared bucket", and a
  sibling project's key refused:

  ```
  viewer lists: ['clinicq/dev/logs/warning/api/2026/09/15/clinicq-20260915-170533.json'] error: None
  message: Issue 197: first log in the shared bucket
  sibling key read: (None, 'Invalid log object key.')
  ```

  The bucket is private (every public access block on) and encrypted with SSE-S3 by default, as AWS creates
  new buckets; this PR sets neither.

## Risk and rollback

- **The first writer fixes the region for every project.** Any project that turns this on with a different
  `AWS_S3_REGION` now finds `btkplatform` already there in `af-south-1` and creates nothing; its client must
  still name `af-south-1` to write.
- **Credentials need `s3:CreateBucket` only while creating.** Without it, the attempt is one warning per
  process and the app carries on.
- **No migration.** Rollback is a revert; the setting is then ignored again, and the bucket stays.

Refs #197
