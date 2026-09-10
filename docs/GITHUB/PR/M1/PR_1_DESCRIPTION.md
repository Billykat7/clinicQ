# PR: Scaffold the ClinicQ application and make the day-one setup true (Issue 1 / M1-01)

This branch turns an empty repository into the application every other issue builds inside: the
FastAPI app factory, typed settings, the `src/` layout, the database baseline, the landing page and
the project's planning docs, scaffolded from the team's existing platform kernel and rebranded for
ClinicQ. The last step, and the reason this PR is ready now, was checking Issue 1's acceptance
criteria against the tree instead of ticking them from memory. Three of them were not true: `mypy
src/` failed on real bugs (two of them would have answered every payment webhook with a `500`), the
README's *Getting started* described commands that did not exist (`uv sync`, `app.main`), and the
spec still asked for a packaging tool and layout the project does not use. Each is fixed here, and
the day-one commands were run end to end on a clean checkout before they were written down.

## Summary

- **Application scaffold:** `create_app()` in `src/main.py` (used by the server and every test
  client), Pydantic settings in `src/core/config.py` with production guards, the `src/` layout, the
  Alembic baseline with PostGIS, the kernel modules (auth, RBAC, audit, notifications and so on), and
  the Makefile and scripts the team runs.
- **Front door:** the ClinicQ landing page (`/`), features, privacy and terms pages, with internal
  delivery sections gated to operators, and the punctuation of every template normalised.
- **Planning docs:** 109 issue specs and 14 milestones rewritten against the real repository
  (starting point, out of scope, how to verify, correct file paths), the issues guide
  (`docs/GITHUB/ISSUES/README.md`), and the GitHub sync script fixed so issues and milestones were
  created in order with working links.
- **Fixes found by the Issue 1 audit:** three runtime bugs behind the `mypy` errors, each with a
  regression test that failed before the fix (see *Design notes*).
- **Day-one setup:** `README.md` *Getting started* rewritten with the five commands that work today,
  and packaging decision 3 recorded.

## Design notes

**Keep `requirements.txt` and the `src/` package (decision 3).** The spec was written for `uv` and an
`app/` package; the scaffold uses `requirements.txt` with setuptools and `src/`. Every script,
Dockerfile, CI stage and guard test already assumes the latter, and switching would be a
repository-wide change with no feature behind it. The decision is recorded in
`docs/GITHUB/ISSUES/README.md` (open decision 3, now decided) and the Issue 1 spec now describes the
layout that exists. Revisiting it is a team decision in its own issue.

**The `mypy` errors were bugs, not annotations.** They were left behind when the kernel was cut down
from the project it came from:

- Both webhook acknowledgement schemas had lost `event_id` while the routes still passed it, and
  both schemas are `extra="forbid"`. Every *verified* Stripe and Paystack webhook therefore raised
  inside the route and answered `500`, so the gateway would retry an event that had already been
  accepted. The Paystack route also read `processed.idempotency_key`, which does not exist. The fix
  restores `event_id` on both schemas (the gateway's own id for Stripe; `<event type>:<reference>`
  for Paystack, whose envelope has no id) and passes `processed.event_id`.
- `ensure_can_reach` passed the recipient's id into `PeerNotReachableError`, whose constructor takes
  no arguments on purpose: the refusal must say nothing about the person the caller tried to reach.
  The call raised `TypeError`, turning the intended `403` into a `500`. It now raises the error with
  no argument, and a comment says why.

**"A required variable is missing" means production.** All 100 settings have safe defaults so that
`make run` works on a fresh clone. What is required is what the production guards refuse to default:
`JWT_SECRET` outside development (the error names it), and the payment keys when a gateway is
enabled. Those guards were already in place and covered by
`tests/unit/security/test_security.py`; this PR documents that reading of the criterion rather than
adding an artificial required variable.

**`DATABASE_URL`, not the `DB_*` parts.** The settings only assemble a URL from `DB_HOST`, `DB_USER`,
`DB_PASSWORD` and `DB_NAME` when `DATABASE_URL` is not a plain `postgresql://` URL, and its default
is one, so the parts on their own are silently ignored. Changing that precedence could affect
deployments and belongs with fail-fast configuration (Issue 12), so this PR documents the working
path (`DATABASE_URL` in `.env`) and leaves the behaviour alone.

**Out of scope:** `.env.example` (Issues 2 and 12), the PostgreSQL 16 versus 18 image (Issue 2), and
the CI workflows (Issue 9).

## Changes

- **`src/schemas/webhooks.py`:** `event_id` restored on `StripeWebhookAck` and `PaystackWebhookAck`.
- **`src/api/v1/routes/webhooks.py`:** the Paystack acknowledgement uses `processed.event_id`.
- **`src/modules/messaging/peers.py`:** `PeerNotReachableError()` raised without the recipient id.
- **`tests/integration/platform/test_webhook_acks.py`:** signed Stripe and Paystack payloads driven
  through signature verification, de-duplication and the acknowledgement; an unsigned payload is
  rejected with `400`.
- **`tests/unit/messaging/test_peers.py`:** the reachability gate refuses without naming the
  recipient, and admits a reachable peer.
- **`README.md`:** *Getting started* rewritten and verified; the tech-stack row says `pip` and
  `requirements.txt` instead of `uv`.
- **`docs/GITHUB/ISSUES/README.md`:** decision 3 recorded.
- **`docs/GITHUB/ISSUES/M1/ISSUE_1_repo_scaffold_app_factory.md`:** context, scope, acceptance
  criteria and *How to verify* describe the layout that exists.
- **Earlier commits on this branch:** the scaffold (`5001133`), the rebrand and landing page
  (`3564b9b`), the front-door pages and internal gate (`a7ba4fe`), punctuation passes (`f4b11c1`,
  `5189540`), the spec and milestone rewrite (`ff41a92`), the sync-script fix (`2b9a4ad`) and the M1
  prompts (`9f74a69`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (153 files, was 4 errors)
- [x] New regression tests: 5 pass, and the 3 that cover the bugs failed before the fixes
      (`extra_forbidden`, missing `idempotency_key`, `TypeError`)
- [x] `make test`: 676 passed. 24 fail exactly as they do without this change: kernel guard tests
      for files that do not exist yet (`.github/workflows/`, `.gitleaks.toml`, `docs/SECURITY/`), to
      be cleared by Issue 9, Issue 7 and the security issues
- [x] Manual check, on a clean checkout with a new virtualenv and no `.env`: every *Getting started*
      command run in order. `pip install -r requirements.txt` took 71 s with no errors;
      `make migrate-up` created 34 tables in `clinicq` with PostGIS; `make run` served `/health`
      (200), `/health/ready` (database and migrations `ok`) and the landing page; `make seed-rbac`
      and `seed-dev-user.sh` then allowed a sign-in as `admin@btk.com` and opened `/dashboard`
- [x] Linux: the Docker image (`python:3.14-slim`) builds, installing the same `requirements.txt`
- [ ] Screenshot: no UI change in this final step; the landing page was reviewed in its own commits

## Acceptance criteria

- [x] `pip install -r requirements.txt` installs cleanly into a fresh Python 3.14 virtualenv on macOS
      and Linux (verified: a macOS clean checkout, and the Linux Docker build). **WSL was not
      tested**: no WSL machine was available; the Linux result is the closest evidence.
- [x] `make run` (`uvicorn src.main:app`) starts and serves a placeholder route (`/health`, `/`)
- [x] `create_app()` is importable and used by both the server (`src/main.py`) and the test client
      (`tests/conftest.py`)
- [x] `ruff check .` and `mypy src/` both pass
- [x] Settings raise a clear startup error when a required variable is missing (`JWT_SECRET` outside
      development: "JWT_SECRET must be set to a secure value outside development")
- [x] `README.md` documents the five commands a new team member needs on day one

## Risk and rollback

The code changes are small and each is covered: two response schemas gain a field (the gateways
ignore the body of an acknowledgement), and one exception is constructed the way its definition
always intended. No migration changes. The documentation changes cannot break a running system.
Rollback is a revert of this PR; nothing depends on it at runtime.

**Follow-ups found during the audit:** the `DB_*` settings being ignored while `DATABASE_URL` has
its default (Issue 12); the kernel guard tests that expect `docs/SECURITY/` files no spec creates
yet (worth an issue of its own); and the Issue 9 note that counts only the unit-test half of those
failures.

Closes #1
