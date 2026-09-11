# Releasing an image

A release is a tag. Pushing `vX.Y.Z` runs [`.github/workflows/release.yml`](../../.github/workflows/release.yml),
which builds the image **once**, checks that exact image, and publishes it to GitHub Container
Registry. Deployment (Issue 11) and rollback only ever *pull* what this produced.

## Cutting one

```bash
git switch main && git pull --ff-only
git tag -a v0.2.0 -m "Release v0.2.0: CI/CD, Environments & Team Workflow"
git push origin v0.2.0
```

Tag a commit on `main`: the ruleset admits nothing there that did not pass CI, so the release
checks the image rather than re-running the tests. Write the release note
(`docs/GITHUB/RELEASES/RELEASE_vX_Y_Z.md`) in the pull request before it.

## What a tag produces

| Tag | Example | Points at |
|-----|---------|-----------|
| `:<git sha>` | `ghcr.io/billykat7/clinicq:0e8d517…` | the image the build pushed, which every check ran against |
| `:<version>` | `ghcr.io/billykat7/clinicq:0.2.0` | the same digest, added once the checks passed |
| `:latest` | `ghcr.io/billykat7/clinicq:latest` | the same digest, moved at the same moment |

All three are one manifest: the version and `latest` tags are added with
`docker buildx imagetools create` from the checked digest, never by building again, and the
workflow fails if the three resolve to different digests. Each release also gets:

- **OCI labels**: `org.opencontainers.image.version`, `.revision` (the commit), `.created` (build
  time) and `.source`, readable with `docker inspect` without starting the image;
- **`/health` reports the same**: `{"status":"ok","version":"0.2.0","git_sha":"<commit>"}`
  (`VERSION` and `GIT_SHA` are baked into the image; never set them in an env file);
- **build provenance** (SLSA v1), signed by this workflow and stored beside the image. To verify it:

  ```bash
  gh attestation verify oci://ghcr.io/billykat7/clinicq:0.2.0 --owner Billykat7
  ```

The package is public, like the repository, so pulling needs no login.

## The checks, before `:latest` moves

| Check | Fails the release when |
|-------|------------------------|
| Size | the uncompressed linux/amd64 image is over `IMAGE_SIZE_CEILING_MB` |
| User | `id -u` in the image is 0 |
| Toolchain | `gcc`, `cc`, `c++`, `g++`, `make`, `pip` or `pip3` is on the image's path |
| Boot | the image, started as production with a throwaway `JWT_SECRET` and no database, does not answer `/health` with `status: ok`, the tag's version and the tagged commit |

A failed check leaves only the `:<git sha>` tag, and it is never deployed; `:latest` and every
version tag stay where they were.

### The size ceiling: 1,000 MB

Agreed in Issue 10. The image was 1,071 MB before that issue (arm64 build). Issue 10 moved the removal
of bytecode caches and pip into the builder stage (deleted in a later layer, they still shipped) and
copied the app as `appuser` instead of re-owning it, and the first release measured **828 MB**
(linux/amd64). About 720 MB of that is the Python dependencies, led by scipy, pandas,
scikit-learn, pyogrio, numpy and matplotlib. The ceiling leaves about 170 MB of headroom for the
next milestones' dependencies. Raising it is a team decision made in review, recorded here, never
a line edited to get a release through.

## Running a published image locally

Against the compose database and Redis (`make db-up`), with nothing else from the repository:

```bash
docker run --rm -p 8010:8000 \
  -e DATABASE_URL=postgresql://btk_user:change-me@host.docker.internal:5432/btk \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  ghcr.io/billykat7/clinicq:0.2.0

curl http://localhost:8010/health         # {"status":"ok","version":"0.2.0","git_sha":"…"}
curl http://localhost:8010/health/ready   # database, migrations and redis: "ok"
```

`host.docker.internal` reaches the host from Docker Desktop; on Linux, add
`--add-host=host.docker.internal:host-gateway`. Change the ports if your stack is not on 5432 and
6379. The image runs development settings unless told otherwise; `ENVIRONMENT=production` needs a
real `JWT_SECRET` (docs/CICD/ENVIRONMENTS.md). To migrate that database with the image's own code:

```bash
docker run --rm -e DATABASE_URL=… ghcr.io/billykat7/clinicq:0.2.0 ./scripts/db/deploy-sequence.sh
```

## Rolling back

A rollback is a deploy of an older tag: nothing is rebuilt, and nothing about the old image has
changed since it was checked. Set `IMAGE` to the version before the bad one (here, back from 0.2.1
to 0.2.0) and bring the stack up; Issue 11's runbook (`docs/CICD/RUNBOOK_DEPLOY.md`) times it.

```bash
IMAGE=ghcr.io/billykat7/clinicq:0.2.0 docker compose -f infra/docker/docker-compose.prod.yml --project-directory . up -d
```

Deploy by version or by commit tag, never by `:latest`: `latest` moves with every release, so a host
deployed "at latest" cannot say what it runs, or roll back to it.

## What a release costs

A tag push is one job. The first release (`v0.0.0-test`, empty layer cache) took **3 min 46 s** from
the push to the attestation, 3 min 1 s of it the build. The layer cache lives in the separate
package `clinicq-buildcache`, so later releases reuse unchanged layers and finish faster. At one or
two releases a month this is under 10 of the 2,000 minutes (docs/CICD/PIPELINES.md).
