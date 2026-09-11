# PR: Make `make check` a gate that can pass, and stop secrets at the commit (Issue 7 / M1-07)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#7](https://github.com/Billykat7/clinicQ/issues/7)

`scripts/ci-local.sh` already ran ruff, mypy, pytest and a Docker build. On `main` it could not
pass, for three reasons:

- The quality stage runs `check-ruff-pin.sh`, which needs the missing `.pre-commit-config.yaml`.
- The secret scan needs the missing `.gitleaks.toml` and baseline.
- 24 kernel guard tests fail because they read files later issues create.

This PR fills those gaps. It adds the pre-commit hooks and the gitleaks configuration, and turns
the 24 into strict expected failures, each naming the issue that clears it. Coverage is now its own
stage, held at the agreed 75%, and the tests get `unit`, `integration` and `slow` markers. A failure
names the stage it happened in. The result is green in 150 seconds with Docker and 42 without. As
the issue asks, it was shown rather than asserted: a commit carrying a fake AWS key was blocked by
pre-commit (see *Testing*).

## Summary

- **`.pre-commit-config.yaml`:** trailing whitespace, end of file, line endings, YAML/TOML/JSON,
  merge markers, case conflicts, files over 500 KB, private keys, ruff (check and format, pinned to
  `requirements.txt`), **gitleaks** on the staged diff, and the ruff pin check. `make hooks` and
  `make hooks-all` now have something to install and run.
- **`.gitleaks.toml` and `.gitleaks-baseline.json`:** the default rules, an allowlist that narrows
  only by value shape (a time-zone name, the compose default password), and the five historical
  test-fixture findings enumerated, redacted, in the baseline.
- **Coverage floor, 75% (agreed):** `[tool.coverage.report] fail_under` in `pyproject.toml`, checked
  by a new `coverage` stage in `ci-local.sh`, separate from the tests, so a red run says which one
  broke.
- **Markers:** `unit` and `integration` applied by location, `slow` on the PostgreSQL and timing
  tests, `--strict-markers`. `make test-fast` (not slow, 15 s) and `make test-unit` join
  `make test`.
- **`ci-local.sh`:** names the failing stage (`✗ ci-local.sh failed at stage: coverage`), prints
  per-stage times, and no longer retries pip-audit when it has an answer. That retry was costing a
  minute on every run.
- **Pending guard tests:** 23 kernel tests that read `.github/workflows/` or `docs/SECURITY/` run
  as `xfail(strict=True)`, listed in one place with the issue each waits for. The 24th, the gitleaks
  guard, passes now.
- **`CONTRIBUTING.md`** (new): setup, the pre-push loop, one module's tests, the database tests,
  coverage, what to do when a commit is blocked for a secret, and the branch and PR conventions.

## Design notes

**Why `make check` could not be green, and why xfail is the honest fix.** The 24 failures all read
files that belong to later issues: 19 read the CI workflows (Issue 9 onwards; Issue 9's own spec
says to expect them), 4 read `docs/SECURITY/` documents that no spec creates yet (raised in
PR #110), and 1 read `.gitleaks.toml`, which this PR adds. There were two alternatives. A red gate
everyone learns to ignore stops being a gate. Skipping the tests when a file is missing would hide
them for good if the file never arrived. `xfail(strict=True)` does neither. The test still runs;
when its file lands it passes, strict reports that pass as a failure, and the issue that made it
pass has to delete its entry. That was checked: with stub security documents in place, the three
`test_the_security_docs_reference_each_other` cases failed with `XPASS(strict)` and their reason.

**The gitleaks hook runs the installed binary.** The upstream `gitleaks` hook builds gitleaks with
Go, and pre-commit downloads a Go toolchain the first time. On this machine that download failed on
Python's certificate store, and on every machine it is slow. `gitleaks-system` runs the binary
`ci-local.sh` already requires (`brew install gitleaks`, 8.25 or newer for `[[allowlists]]`), so the
commit hook and the pre-push history scan are the same tool reading the same config. Without the
binary the hook fails the commit; it never skips the scan. The upstream definition passes file
names to `gitleaks git`, which does not accept them, so the config sets `pass_filenames: false`.

**Allowlist by shape, baseline by finding.** The kernel's own guard test explains why `paths` is
forbidden: gitleaks drops those files before any rule runs, so `paths = ['^tests/']` would let a
real key in a test file through. The allowlist therefore matches values that cannot be secrets.
Everything else that was reviewed goes into the baseline one finding at a time: four test passwords
and JWT secrets from the scaffold, and the fake token the Issue 6 redaction test masks. The baseline
is gitleaks' own redacted report, so it contains no secret text.

**75%, and a stage of its own.** Line coverage of `src/` was 76% when the team agreed the floor. At
that level coverage cannot slip unnoticed, and the kernel code M3 to M6 will rewrite is not forced
into tests it is about to lose. The floor lives in `pyproject.toml`, so `ci-local.sh`, a developer's
`coverage report` and CI (Issue 9) read one number. Tests collect coverage (`--cov-fail-under=0`)
and the next stage judges it, so "tests failed" and "coverage dropped" are different messages.

**Markers by location, not by hand.** Every test under `tests/unit/` is `unit` and everything else
`integration`, so nobody has to remember, and the directory layout the testing rule already
prescribes decides. `slow` is the exception that needs a decision: the PostgreSQL tests (a server,
seconds each), the event-loop benchmark and the liveness timing test. `--strict-markers` turns a
misspelt marker into an error rather than an empty selection. `make test` stays "everything", as CI
runs it; the fast loop is `make test-fast`, which is what the spec's "default fast selection"
means for a developer.

**A minute saved in pip-audit.** It found real advisories (below) and exited non-zero, and the
script took that for a flaky network, retrying three times with pauses. It now retries only when
pip-audit produced no report at all: `make check-fast` went from 98 s to 42 s.

**Out of scope:** the GitHub Actions workflow (Issue 9) and factories (Issue 8).

## Changes

- **`.pre-commit-config.yaml`**, **`.gitleaks.toml`**, **`.gitleaks-baseline.json`** (new).
- **`scripts/ci-local.sh`:** stage bookkeeping with an EXIT trap that names the failed stage;
  per-stage times in the summary; a `coverage` stage; tests collect coverage; pip-audit retries
  only without a result; the unused `*_PASSED` flags removed (shellcheck clean).
- **`pyproject.toml`:** `--strict-markers`, the four markers, `[tool.coverage.run]` and
  `[tool.coverage.report]` with `fail_under = 75`.
- **`tests/conftest.py`:** markers by location; `PENDING_ON_LATER_ISSUES`.
- **`tests/integration/platform/test_event_loop_stall_benchmark.py`**,
  **`test_request_context.py`:** marked `slow`.
- **`Makefile`:** `test-fast`, `test-unit`. **`CONTRIBUTING.md`** (new); **`README.md`** links it.
- **Whitespace:** `trailing-whitespace` fixed `.gitignore` and `docs/IDE/PROMPTS/M1.md` on its
  first run (`git diff -w` is empty).
- **Docs:** the Issue 7 spec (the agreed floor, the pending guard tests, files touched).

## Testing

- [x] `make check`: **green in 150 s** with a warm Docker cache: quality 1 s, pip-audit 10 s,
      secrets 0 s, tests 29 s (886 passed, 12 PostgreSQL tests skipped, 23 xfailed), coverage 1 s
      (**76.9%**), docker 5 s, then Trivy. `make check-fast`: **42 s**
- [x] **Pre-commit blocks a commit containing a fake AWS key.** A file with a freshly generated
      `AKIA…` id and a 40-character secret key was staged and committed:

      ```text
      $ git commit -m "Issue 7: this commit must be blocked"
      detect private key.......................................................Passed
      ruff check...............................................................Passed
      ruff format..............................................................Passed
      Detect hardcoded secrets.................................................Failed
      - hook id: gitleaks-system
      - exit code: 1
      Finding:     ...WS_ACCESS_KEY_ID = "REDACTED
      RuleID:      aws-access-token
      File:        fake_aws_credentials.py
      Line:        3
      Finding:     AWS_SECRET_ACCESS_KEY = "REDACTED"
      RuleID:      generic-api-key
      File:        fake_aws_credentials.py
      Line:        4
      WRN leaks found: 2
      git commit exit=1
      $ git log --oneline -1
      bb31264 Merge pull request #115 …     # unchanged: nothing was committed
      ```

- [x] **The script stops at the first failing stage and names it.** Three real runs:

      ```text
      a file needing formatting:  Would reformat: tests/conftest.py
                                  ✗ ci-local.sh failed at stage: quality (after 1s, exit 1)
      a test broken on purpose:   FAILED tests/unit/test_issue7_demo_broken.py::test_broken_on_purpose
                                  1 failed, 886 passed, 12 skipped, 23 xfailed
                                  ✗ ci-local.sh failed at stage: tests (after 36s, exit 1)
      the floor raised to 99:     Coverage failure: total of 76.9 is less than fail-under=99.0
                                  ✗ ci-local.sh failed at stage: coverage (after 31s, exit 1)
      ```

- [x] `pytest -m unit`: **566 passed in 7.4 s** wall (under 30 s); `make test-fast`: 883 passed in
      15 s
- [x] `pre-commit run --all-files`: every hook passes (5.6 s)
- [x] gitleaks over the full history with the config and baseline: `no leaks found`; without the
      baseline, 5 findings, each a reviewed test fixture
- [x] Strict xfail: with stub `docs/SECURITY/` files, the three pending cases fail as
      `XPASS(strict)` with their reason
- [x] `shellcheck -S warning scripts/ci-local.sh`: clean. `ruff`, `mypy src/` (160 files): clean
- [ ] A cold `make check` (no Docker cache) is slower: the image build downloads Python and
      reinstalls `requirements.txt`. The under-3-minutes figure is for the everyday warm case

## Acceptance criteria

- [x] `./scripts/ci-local.sh` completes in under 3 minutes on a mid-range laptop (150 s, warm Docker
      cache; 42 s without Docker)
- [x] The script exits non-zero on the first failing stage and prints which stage failed (quality,
      tests, coverage shown above)
- [x] Pre-commit blocks a commit containing an obvious secret (shown above)
- [x] `pytest -m unit` runs in under 30 seconds (7.4 s)
- [x] Coverage below the agreed threshold fails the script (the `coverage` stage, shown above)
- [x] `CONTRIBUTING.md` documents the pre-push workflow every team member follows

## Risk and rollback

Hooks change what happens on `git commit` for anyone who runs `make hooks`: a commit with trailing
whitespace is fixed and has to be re-staged, and without gitleaks installed every commit fails until
it is (by design, and documented). Nothing changes for a clone that has not installed the hooks. The
23 pending tests stop failing and start expecting failure. No application code changes. Rollback is
a revert of this PR.

**Follow-ups found along the way:**

- pip-audit and Trivy both report **httpx2 2.9.1** (PYSEC-2026-3845 and 3848, fixed in 2.11.0) and
  **httpcore2 2.9.1** (PYSEC-2026-3844, CVE-2026-84381, fixed in 2.10.0): multipart header injection,
  and `wss://` over SOCKS5 without TLS. The app uses neither feature, but the pins should move; that
  is a small change on its own.
- Issue 9 should run the same stages in CI, including the coverage floor, `make test-postgres` with
  `REQUIRE_POSTGRES_TESTS=1`, and the gitleaks scan, and delete the 19 workflow entries from
  `PENDING_ON_LATER_ISSUES` as they start passing.
- The four `docs/SECURITY/` guards need an issue that writes those documents.

Closes #7
