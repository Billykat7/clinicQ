# PR: Raise httpx2 and httpcore2 to 2.12.0 so both scanners pass clean (Issue 7 / M1-07 follow-up)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#7](https://github.com/Billykat7/clinicQ/issues/7) (closed by #116; this is its
follow-up)

The two security scans Issue 7 added to `make check`, pip-audit and Trivy, have warned on every
run since they landed. The warnings were about httpx2 2.9.1 and httpcore2 2.9.1. This PR raises
both to 2.12.0, and `./scripts/ci-local.sh` now reports **no known vulnerabilities** from pip-audit
and **0 findings on every target** from Trivy. The only change is two lines in `requirements.txt`;
no code or test changed.

## Summary

- **`httpx2==2.9.1` becomes `httpx2==2.12.0`.** It is a direct pin: starlette's `TestClient`
  imports `httpx2` ahead of `httpx`, and one benchmark test imports it.
- **`httpcore2==2.12.0` is added.** It was not pinned before, because it arrives only through
  httpx2. httpx2 requires it at exactly its own version (`httpcore2==2.12.0`), so it is now pinned
  alongside it, like `greenlet`, and the comment says to raise the two together.

| Advisory | Package | Problem | Fixed in |
|----------|---------|---------|----------|
| PYSEC-2026-3845 | httpx2 | `wss://` through a SOCKS5 proxy sent without TLS | 2.10.0 |
| PYSEC-2026-3844 / CVE-2026-84381 | httpcore2 | the same flaw, in the transport | 2.10.0 |
| PYSEC-2026-3848 | httpx2 | multipart part-header CRLF injection | 2.11.0 |
| PYSEC-2026-3846 / CVE-2026-84382 | httpx2 | decompression memory amplification (denial of service) | **2.12.0** |

## Design notes

**Why 2.12.0 and not 2.11.0.** 2.11.0 is the first release that fixes the three advisories
reported against 2.9.1, and it was tried first. Both scanners then reported a fourth, HIGH
advisory against 2.11.0 itself (the last row above). 2.12.0 fixes it and has the same
requirements, so it is the first version that passes clean.

**Where the pins come from.** `pip show` gives `httpx2  Required-by: clinicq` and
`httpcore2  Required-by: httpx2` at 2.9.1. Neither FastAPI (0.141.1) nor Starlette (1.3.1)
declares either package; starlette imports httpx2 only when `starlette.testclient` is used. So
nothing else constrains the versions, and `pip check` is clean after the bump.

**Exact pins, like the rest of the file.** A `>=` floor would let each build resolve a different
httpx2. With `==`, the Docker image and every laptop get the version the scanners checked.

**Out of scope:** `httpx==0.28.1` stays. No advisory lists it, and removing it is its own change.

## Changes

- **`requirements.txt`:** `httpx2==2.12.0`, `httpcore2==2.12.0`, and a comment naming the
  advisories (+8, −1).
- **`docs/GITHUB/PR/M1/PR_7_FOLLOWUP_DESCRIPTION.md`** (this file).

## Testing

- [x] **Before, at 2.9.1** (`make check-fast`, pip-audit step, trimmed):

      ```text
      httpx2    2.9.1   PYSEC-2026-3845 2.10.0   httpcore2 does not start TLS for `wss://` connections …
      httpx2    2.9.1   PYSEC-2026-3848 2.11.0   HTTPX2 serializes the per-file `Content-Type` …
      httpcore2 2.9.1   PYSEC-2026-3844 2.10.0   httpcore2 does not start TLS for `wss://` connections …
      ```

      Trivy reported the same three on the image (recorded in PR #116 and the v0.1.0 release
      notes; not re-run at 2.9.1 for this PR).
- [x] **Tried, at 2.11.0:** `./scripts/ci-local.sh` passed, but both scanners still warned:

      ```text
      pip-audit: httpx2 2.11.0  PYSEC-2026-3846  2.12.0  When decoding a compressed response body …
      Trivy:     httpx2 (METADATA) │ CVE-2026-84382 │ HIGH │ fixed │ 2.11.0 │ 2.12.0 │
                 Denial of Service via streaming response decompression memory amplification
      ```

- [x] **After, at 2.12.0:** `source .venv/bin/activate && ./scripts/ci-local.sh`, exit 0:

      ```text
      STEP 1b: Dependency scan (pip-audit, warn-only)
      No known vulnerabilities found
      ✅ pip-audit passed
      935 passed, 14 skipped, 23 xfailed, 11 warnings in 19.19s
      TOTAL                                      10013   2229  77.7%
      ==> trivy image --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed clinicq-app:local
      opt/venv/lib/python3.14/site-packages/httpcore2-2.12.0.dist-info/METADATA  python-pkg  0
      opt/venv/lib/python3.14/site-packages/httpx2-2.12.0.dist-info/METADATA     python-pkg  0
      ✅ Docker build and Trivy scan passed
      ✅ All CI checks passed locally in 164s
         quality 0s · pip-audit 14s · secrets 1s · tests 21s · coverage 0s · docker 93s
      ```

      All 133 targets in Trivy's report summary (the Debian 13.6 base and every Python package)
      show 0 vulnerabilities. The Docker build installed `httpx2-2.12.0` and `httpcore2-2.12.0`
      from `requirements.txt`.
- [x] **Nothing else moved.** The suite gives the same result as before the bump (935 passed,
      14 skipped, 23 xfailed), and coverage stays at 77.7%. `pip check` reports "No broken
      requirements found" after `pip install -e ".[dev]"`, which also refreshes the editable
      install's metadata.
- [x] **Every pre-commit hook passed on the commit,** including the ruff-pin check that reads
      `requirements.txt`.
- [ ] **Not run:** the 14 PostgreSQL tests (`make test-postgres`). They exercise the database
      layer, not the HTTP client.

## Acceptance criteria

- [x] httpx2 is at 2.11.0 or later (2.12.0) and httpcore2 at 2.10.0 or later (2.12.0), and every
      other pin still resolves (`pip check`).
- [x] pip-audit no longer lists httpx2 or httpcore2; it finds no known vulnerabilities.
- [x] Trivy no longer reports httpx2 or httpcore2; the image has no CRITICAL or HIGH finding.
- [x] No test broke, so no API change needed a fix.

## Risk and rollback

This is a minor-version bump of a client that the app uses only through starlette's `TestClient`
and one benchmark test, so the application's runtime path does not load it. The whole suite
passes unchanged. Rollback is a revert of this PR, which brings back the four advisories.

**Follow-up:** the v0.1.0 release notes (`docs/GITHUB/RELEASES/RELEASE_v0_1_0.md`, not yet
pushed) list these advisories under *Known issues*. That entry comes out once this PR is merged.

Refs #7 (already closed by #116; this PR closes nothing new)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
