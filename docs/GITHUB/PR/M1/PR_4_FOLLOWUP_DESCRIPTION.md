# PR: Remove the 55 property-management exceptions nothing references (Issue 4 / M1-04 follow-up)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#4](https://github.com/Billykat7/clinicQ/issues/4) (closed by #112; this is its
follow-up)

`src/commons/exceptions.py` held 79 classes. Issue 4 (#112) added the six category bases and
reparented the 16 exceptions the kernel raises. That left 55 classes behind. They are residue of the
property-management project the kernel was scaffolded from, and nothing raises, catches, imports or
names them. This PR deletes them, and nothing else: 937 lines removed, none added. The module goes
from 1,332 lines to 395, and every check gives the same result as on `main`.

## Summary

- **Deleted (55):** the unit, application, tenant, tenancy, lease, lease-template, payment,
  late-fee, statement, maintenance, work-order, vendor, inspection and deduction exceptions, plus
  `EsignEnvelopeNotFoundError`, `LeaseSignatureRequiredError`, `StripePaymentError` and
  `PaystackPaymentError`.
- **Kept:** the module docstring, `ErrorEnvelope`, `BKPropertyError`, the six category bases
  (`NotFoundError`, `ConflictError`, `UnprocessableError`, `ForbiddenError`, `UpstreamError`,
  `ServiceUnavailableError`) and the 16 exceptions the kernel raises.

## Design notes

**Deleted by evidence, not by name.** Each class was grepped as a whole word across `src/`,
`tests/`, `scripts/` and `alembic/`, with its own `class X(` line excluded. So a class survives if
it is only subclassed or mentioned in a docstring elsewhere in the file. Only classes with zero hits
went. The deletion was done with Python's `ast` module, by each class's exact line range, not by
hand.

**No indirect use either.** Nothing in those four trees calls `__subclasses__`, calls `getattr` on
the module or star-imports it, so no class can be reached without its name appearing. `docs/`,
`README.md` and `CONTRIBUTING.md` do not mention any of the 55.

**Their error codes go with them.** Codes such as `clinicq.unit.illegal_status_transition` and
`applications.illegal_status_transition` were never sent to a client, because nothing raised them.
No contract changes.

**The name `BKPropertyError` stays.** Renaming the base touches every kept exception and every
handler, which is a separate change.

## Changes

- **`src/commons/exceptions.py`:** 55 class definitions deleted (−937 lines, +0).
- **`docs/GITHUB/PR/M1/PR_4_FOLLOWUP_DESCRIPTION.md`** (this file).

## Testing

- [x] **Reference count per class**, excluding the definition (trimmed; 55 lines of `0`):

      ```text
      $ for c in $(grep -oE "^class [A-Za-z_]+" src/commons/exceptions.py | awk '{print $2}'); do
          n=$(grep -rnw --exclude-dir=__pycache__ "$c" src tests scripts alembic \
              | grep -v "^src/commons/exceptions.py:[0-9]*:class $c(" | wc -l)
          echo "$n $c"; done | sort -n
      0 ApplicationNotAcceptedError
      0 ApplicationStatusTransitionError
      …                                   (55 classes at 0)
      0 WorkOrderStatusTransitionError
      2 UpstreamError
      4 ServiceUnavailableError
      4 UnprocessableError
      5 EsignEnvelopeStateError
      5 ForbiddenError
      5 InAppNotificationNotFoundError
      5 InvalidImageError
      …
      13 MessageDraftNotFoundError
      71 BKPropertyError
      ```

      Every kept class is referenced outside `exceptions.py`: the 16 raised ones by the code that
      raises or catches them, and the category bases by `tests/integration/platform/test_error_envelope.py`.
- [x] **No indirect lookup:** `grep -rnE "__subclasses__|getattr\((exc|exceptions)|commons\.exceptions import \*"`
      over `src tests scripts alembic` finds nothing.
- [x] **The requested check gives the same result on the branch as on `main`:**

      ```text
      $ ruff check . && ruff format --check . && mypy src/ && pytest tests/ -q -n auto --dist loadscope
      main (33a2980):   All checks passed! · 277 files already formatted ·
                        Success: no issues found in 160 source files ·
                        935 passed, 14 skipped, 23 xfailed, 11 warnings in 17.63s
      branch (9fcbda1): All checks passed! · 277 files already formatted ·
                        Success: no issues found in 160 source files ·
                        935 passed, 14 skipped, 23 xfailed, 11 warnings in 18.25s
      ```

- [x] **The 14 PostgreSQL tests pass on the branch:** `make test-postgres DB_PORT=5433` gives
      `14 passed, 958 deselected in 25.54s`.
- [x] **`make check-fast`:** green in 49 s. Coverage rises from 76.9% to **77.7%**, because the
      deleted lines were never executed. pip-audit still reports the known httpx2 and httpcore2
      advisories, which this PR does not touch.
- [x] **Every pre-commit hook passed on the commit,** gitleaks included.

## Acceptance criteria

- [x] Every class with zero references in `src/`, `tests/`, `scripts/` and `alembic/` is deleted
      (55 of them).
- [x] The module docstring, `ErrorEnvelope`, `BKPropertyError`, the six category bases and every
      referenced class are kept (24 classes).
- [x] `ruff check`, `ruff format --check`, `mypy src/` and the full test suite give the same result
      as on `main`.

## Risk and rollback

This PR only deletes classes nothing refers to. An import of one of them anywhere in the repo would
have failed mypy or a test, and the grep above found none. Code outside this repository cannot
import them; the kernel is not published as a library. Rollback is a revert of this PR.

Refs #4 (already closed by #112; this PR closes nothing new)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
