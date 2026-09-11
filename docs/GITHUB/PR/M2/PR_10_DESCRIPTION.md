# PR: Publish one checked image to GHCR per version tag, under three tags (Issue 10 / M2-10)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#10](https://github.com/Billykat7/clinicQ/issues/10)

Deployment needs an artefact that never changes once it is checked. This PR adds
`.github/workflows/release.yml`: a `v*.*.*` tag builds the image once, pushes it as `:<git sha>`,
checks that exact digest (size ceiling, non-root, no toolchain, boots as production and reports its
version and commit), then points `:<version>` and `:latest` at the same manifest and signs its build
provenance. The existing multi-stage Dockerfile stays; it gains the version, commit and build time
(reported at `/health` and as OCI labels) and loses 190 MB it was shipping by accident. A
throwaway tag, `v0.0.0-test`, published in 3 minutes 46 seconds from an empty cache. A second one
took 1 minute 16 seconds, and rolling back from it to the first took 5.5 seconds, with nothing rebuilt.

## Summary

- **`release.yml`, tag-only.** Build and push `:<sha>` (linux/amd64, SLSA provenance and SBOM
  attestations, a registry layer cache in its own package), pull it by digest, check it, add
  `:<version>` and `:latest` with `docker buildx imagetools create` (a new tag, not a new build),
  verify all three resolve to one digest, and attest. Releases queue and are never cancelled.
- **The image says what it is.** `ARG VERSION / GIT_SHA / BUILD_TIME` become `ENV VERSION`, `ENV
  GIT_SHA` and the `org.opencontainers.image.*` labels. `/health` now answers
  `{"status":"ok","version":"0.0.0-test","git_sha":"0e8d517…"}`; `GIT_SHA` is a new setting.
- **190 MB it should never have shipped.** The runtime stage deleted the venv's `.pyc` files and pip
  *after* copying the venv in, and a file deleted in a later layer still ships in the layer that
  added it. Doing it in the builder stage, and copying the app as `appuser` instead of
  `chown -R`-ing it into a second layer, takes the image from 1,071 MB to 881 MB (arm64 build);
  the release's linux/amd64 image is 828 MB.
- **A 1,000 MB ceiling**, checked in the workflow, and documented with the measurement behind it.
- **`.dockerignore`, as an allowlist.** There was none, so every build sent the repository's `.env`
  files to the build daemon. Now only `requirements.txt`, `src/`, `alembic*` and `scripts/db/`
  enter the context.
- **`scripts/db/` in the image**, so a deploy migrates with the code it is about to run
  (`./scripts/db/deploy-sequence.sh`, Issue 9's single definition; Issue 11 calls it).
- **`docs/CICD/RELEASE.md`:** cutting a release, the three tags, the checks, the ceiling, the
  documented `docker run` against a local database, and rollback.

## Design notes

**Check the digest you will publish, then tag it.** Building once for the checks and again to push
would publish an image nobody checked (a new `created` timestamp alone changes the digest). So the
build pushes only the commit tag; every check pulls that digest; only then do `:<version>` and
`:latest` point at it. If a check fails, only `:<sha>` exists, nothing deploys it, and `:latest`
has not moved.

**No second test run on the tag.** The tagged commit is on `main`, and the ruleset from Issue 9
admits nothing to `main` without a green CI gate. Re-running the suite would roughly double a
release's time for no new information about the code. The release checks what CI could not: the
image.

**Registry cache in its own package.** Actions caches are scoped to a ref, and a tag cannot read
another tag's cache. The layer cache therefore lives in the registry, in `clinicq-buildcache`, so
the app package holds only release tags and every release reuses the last one's layers. The second
release's build took 26 s against 3 min 1 s cold.

**Why these checks.** Size is the criterion's ceiling. `id -u` and a search for `gcc`, `cc`, `c++`,
`g++`, `make`, `pip`, `pip3` are the non-root and "no toolchain" criteria. Booting as *production*
(a throwaway `JWT_SECRET`, no database) proves the image passes the Issue 12 guards and serves
`/health` with the tag's version and commit; liveness touches no dependency, so no database is
needed. `apt-get` stays: it is the Debian base's package manager, not a compiler.

**`:latest` moves for pre-release tags too**, because the issue's own verification step expects it
for `v0.0.0-test`. Nothing deploys `:latest` (the deploy uses the version or commit tag); it is a
convenience pointer, and v0.2.0 will move it back to a real release.

**Out of scope:** deploying (Issue 11), and trimming the dependencies. About 720 MB of the image is
Python packages (scipy, pandas, scikit-learn, pyogrio, numpy, matplotlib) that the kernel imports;
removing any is a code change with its own issue.

## Changes

- **`.github/workflows/release.yml`** (new): the workflow above.
- **`infra/docker/Dockerfile`:** venv stripping moved to the builder stage; `COPY --chown`; `scripts/`
  (`__init__.py`, `db/`) copied; `VERSION`, `GIT_SHA` and `BUILD_TIME` build arguments, env and OCI
  labels, declared last so a new commit rebuilds only metadata layers.
- **`.dockerignore`** (new, repository root, as `.cursor/rules/infra-layout.mdc` requires): the allowlist.
- **`src/core/config.py`:** `GIT_SHA`; the `VERSION` description says the image sets it.
  **`src/schemas/health.py`**, **`src/main.py`:** `git_sha` in the liveness response.
  **`.env.example`:** regenerated (`# GIT_SHA=unknown`, documented as image-set).
- **`tests/unit/platform/test_workflow_guardrails.py`:** `release.yml` leaves `NOT_YET_CREATED`, and four
  guards: version tags are the only trigger; releases are never cancelled; `:latest` moves only
  after the size, non-root and `/health` checks, from the digest the build pushed as `:<sha>`; and
  there is a ceiling that fails the release.
- **`tests/integration/platform/test_health.py`:** `/health` reports the version and commit the
  settings carry (the alias `VERSION`, as the image sets it).
- **`docs/CICD/RELEASE.md`** (new); **`docs/CICD/PIPELINES.md`:** the tag row and the release's minutes.

## Testing

- [x] **The throwaway release**, `v0.0.0-test` on `0e8d517` (run 34626519666): every step green,
      **3 min 46 s** from the push to the attestation, 3 min 1 s of it the cold build.

      ```text
      Image size: 828 MB uncompressed (ceiling 1000 MB)
      uid=999(appuser) gid=999(appuser) groups=999(appuser)
      GET /health → {"status":"ok","version":"0.0.0-test","git_sha":"0e8d51706d069e6b43b53750ceab9c1464958523"}
      ```

- [x] **The image in GHCR, with three tags on one manifest**, pulled anonymously on this machine (the
      package inherited the repository's public visibility):

      ```text
      :0e8d51706d069e6b43b53750ceab9c1464958523   sha256:a85c4d6483f238043324f73fc5772da2ffc47cae2d3bc46a978baa4928a37fc3
      :0.0.0-test                                 sha256:a85c4d6483f238043324f73fc5772da2ffc47cae2d3bc46a978baa4928a37fc3
      :latest                                     sha256:a85c4d6483f238043324f73fc5772da2ffc47cae2d3bc46a978baa4928a37fc3
      ```

- [x] **`docker run --rm ghcr.io/billykat7/clinicq:0.0.0-test id`** → `uid=999(appuser) gid=999(appuser)`.
- [x] **The documented `docker run` against the local database** (the compose PostGIS and Redis on
      this machine's ports 5433 and 6380):

      ```text
      {"status":"ok","version":"0.0.0-test","git_sha":"0e8d51706d069e6b43b53750ceab9c1464958523"}
      {"status":"ok","checks":{"database":"ok","migrations":"ok","redis":"ok","storage":"skipped"}}
      ```

      And `./scripts/db/deploy-sequence.sh` inside the published image, on an empty database:
      `Running upgrade -> 0001`, `rbac seed: … permissions +107 …`, `rbac seed --check: in sync`, exit 0.
- [x] **No toolchain:** `gcc`, `cc`, `g++`, `make` and `pip` absent (in the workflow and locally).
      **Labels:** `org.opencontainers.image.version=0.0.0-test`, `.revision=0e8d517…`,
      `.created=2026-09-11T17:14:12Z`, `.source=https://github.com/Billykat7/clinicQ`.
- [x] **Provenance:** `gh attestation verify oci://ghcr.io/billykat7/clinicq:0.0.0-test --owner Billykat7`
      exits 0: SLSA v1 provenance, subject digest `sha256:a85c4d…`, signed by
      `.github/workflows/release.yml@refs/tags/v0.0.0-test`.
- [x] **A second release and a rollback, nothing rebuilt:** `v0.0.1-test` on `5ccd2e7` (run
      34627205663) took **1 min 16 s** (build 26 s, warm cache) and moved `:latest` to
      `sha256:5670910b…`, while `:0.0.0-test` stayed `sha256:a85c4d…`. With `:0.0.1-test` running,
      the rollback replaced it with `:0.0.0-test`:

      ```text
      running now:    {"status":"ok","version":"0.0.1-test","git_sha":"5ccd2e7a1e85de761669b9764785cd11d92f4f44"}
      after rollback: {"status":"ok","version":"0.0.0-test","git_sha":"0e8d51706d069e6b43b53750ceab9c1464958523"}
      rollback (stop, start the older tag, healthy): 5.5 s, image already pulled, nothing built
      ```

      Pulling `:0.0.1-test` found all 16 of its filesystem layers already present from the first
      release: two releases of the same dependencies differ only in the metadata layers.
- [x] **The Dockerfile change on its own:** a local build went from 1,071 MB to 881 MB; the runtime
      venv holds no `__pycache__` (`find … | wc -l` → 0).
- [x] **CI on this PR** green in 2 min 14 s, including the `Image builds` job (the Dockerfile changed)
      in 26 s. **The suite:** 984 passed, 16 skipped, 11 xfailed.

## Acceptance criteria

- [x] Pushing a release tag publishes an image to GHCR within 6 minutes: `v0.0.0-test` in 3 min 46 s
      from an empty cache, `v0.0.1-test` in 1 min 16 s. `v0.2.0` itself is pushed when M2 closes.
- [x] The image runs as a non-root user and contains no build toolchain (`uid=999(appuser)`; no
      compiler, `make` or `pip`; both checked by the release on every tag).
- [x] `docker run` of the published image serves `/health` successfully (above, locally and in the
      workflow).
- [x] The image reports its version and git sha at `/health` (`"version":"0.0.0-test","git_sha":"0e8d517…"`).
- [x] Image size stays under an agreed ceiling, checked in the workflow: 1,000 MB, measured 828 MB.
      The ceiling is **proposed here for the team to agree in review**, with its reasoning in
      `docs/CICD/RELEASE.md`.
- [x] Rolling back means deploying a previous tag, with no rebuild required (`:0.0.0-test` kept its
      digest after `:0.0.1-test`, and running it took 5.5 s).

## Risk and rollback

Nothing in this PR runs on a pull request, except the image build CI already did. The Dockerfile
changes only *where* files are removed, and the image's contents are the same apart from the new
`scripts/db/` and the metadata. The workflow and the local runs above confirmed the image's
behaviour. The release workflow can do only what a tag asks it to, and a failed check stops before
`:latest` moves. Rollback of this PR is a revert; images already published stay in GHCR and stay
deployable.

**Left in place on purpose:** the throwaway tags `v0.0.0-test` and `v0.0.1-test` (git tags and GHCR
images), because Issue 11 uses two releases to demonstrate its deploy and a timed rollback.
`:latest` points at `0.0.1-test` until `v0.2.0` moves it. Deleting the tags afterwards needs your
go-ahead.

Closes #10

🤖 Generated with [Claude Code](https://claude.com/claude-code)
