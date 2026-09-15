# S3 storage layout

One bucket, `btkplatform`, is shared by the sibling projects (clinicq, properties, umojanet, maps,
and the rest). Every object key starts with the project that wrote it, then the environment, then
the kind of object:

```
{bucket}/{PROJECT_SLUG}/{dev|uat|prod}/{logs|docs|backups|...}/...
```

| Setting | Value here | Notes |
|---------|------------|-------|
| `AWS_S3_BUCKET` | `btkplatform` | the same in every sibling project |
| `PROJECT_SLUG` | `clinicq` (the default) | lowercase letters, digits and hyphens; the app refuses anything else |
| `ENVIRONMENT` | `development` / `staging` / `production` | written into keys as `dev` / `uat` / `prod` |

`Settings.s3_prefix(kind)` in `src/core/config.py` is the one place the prefix is built:
`s3_prefix("logs")` in development is `clinicq/dev/logs/`.

## What clinicQ writes

| Kind | Key | Written by |
|------|-----|------------|
| Structured logs | `clinicq/{env}/logs/{info\|warning\|error}/{api\|web\|worker}/{YYYY}/{MM}/{DD}/clinicq-{YYYYMMDD}-{HHMMSS}.json` | `src/core/s3_logging.py` (when `AWS_S3_LOGGING_ENABLED=true`) |
| Readiness sentinel | `clinicq/{env}/logs/_readiness/probe.json` (one object, overwritten) | `/health/ready` |
| Database backups | `clinicq/{env}/backups/…` when `BACKUP_S3_URI=s3://btkplatform/clinicq/prod/backups/` | `scripts/db/backup.sh` |

The log viewer (`/logs`, `GET /api/v1/admin/logs`) lists and reads only under
`clinicq/{env}/logs/`: a sibling project's logs in the same bucket are never listed, and a request
for one of their keys is refused before S3 is called.

## Access policy

Because the bucket is shared, give each project's IAM user its own prefix only. For clinicQ:
`s3:PutObject` and `s3:GetObject` on `arn:aws:s3:::btkplatform/clinicq/*`, and `s3:ListBucket` on
`arn:aws:s3:::btkplatform` with the condition `StringLike` `s3:prefix` = `clinicq/*`. A lifecycle
rule can then expire logs per project (`clinicq/`) or across all of them.

## Objects written before this layout

Keys without the slug (`dev/logs/...`) are not read by the viewer. They were written to the older
per-project buckets (`btkcapetour`, `btkhomes`); nothing needs moving into `btkplatform`.

## Creating the bucket

With `AWS_S3_CREATE_BUCKET_IF_MISSING=true` the app creates `AWS_S3_BUCKET` in `AWS_S3_REGION` before its
first write (the log handler's first upload, or the readiness probe), once per process:

- `HeadBucket` answers 404: the bucket is created. `us-east-1` gets no `LocationConstraint`; every other
  region does.
- `HeadBucket` succeeds: nothing happens.
- `HeadBucket` answers 403: the name exists but is someone else's, or not readable with these credentials.
  Nothing is created, and a warning names the code.
- `CreateBucket` fails (for example no `s3:CreateBucket`): a warning, never an error; the next write checks
  again. `BucketAlreadyOwnedByYou` (another process won the race) counts as created.

The setting is off by default. A bucket's region cannot be changed after it is created, so the first project
to create `btkplatform` fixes its region for all of them: agree `AWS_S3_REGION` across the projects first.
Give the credentials `s3:CreateBucket` only while the bucket is being created.
