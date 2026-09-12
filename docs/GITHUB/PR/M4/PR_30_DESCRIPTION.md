# PR: The sites and queues API, written down and checked against itself (Issue 30 / M4-30)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#30](https://github.com/Billykat7/clinicQ/issues/30) · **Builds on:** #23–#29 · **Closes the milestone.**

> **Merge order:** last. This branch is stacked on #24–#29, so the Conventions check fails on their
> commits until they merge. Every test job passes.

The sites module is consumed by discovery, the dashboard, the board and both channel adapters. A
hand-written contract with a drift test means those consumers can be built against a stable document
instead of against whatever the code happens to return this week.

## Summary

- **`contracts/sites.yaml`**, hand-written from the routers as they actually are: **45 operations**
  across 31 paths, 54 schemas, error responses documented alongside the happy paths, and examples
  throughout.
- **A reusable drift harness**, `tests/integration/contracts/test_openapi_contracts.py`. Adding a
  contract is **one `Contract(...)` line**; every test is parameterised over that list. Discovery
  (38), queue (47), notifications (71), channels (79) and reporting (94) inherit all of it.
- **Both directions.** An undocumented route fails the suite and names it; a documented path with no
  handler fails too.
- **Every example validates against its own schema**, resolving the contract's own `$ref`s.
- **`tests/integration/sites/test_sites_module.py`**: the full lifecycle in one place, and **every
  documented refusal driven over real HTTP**, because the drift test structurally cannot see them.
- **`contracts/README.md`**: the five rules, and how the next milestone adds its contract.
- **M4's exit criteria ticked**, with the one partial written out rather than ticked silently.

## Design notes

**Who wrote what.** The spec names A as owner; the sprint plan puts the drift harness in E's lane
and the contract review in F's, and asks that this be agreed before sprint 5. It was: **E owns the
harness** (`test_openapi_contracts.py`, `contracts/README.md` and the reusable `Contract` shape),
**F owns the contract review** (reading `sites.yaml` against the routers, and the error responses
and examples in it), and **A owns the module suite**, which is the part that needs to know what each
handler actually refuses. This PR carries all three because M4 closes here; the split is what the
next five contracts follow.

**The drift test is narrower than it first appears, and saying so is the point.** FastAPI's own
`/openapi.json` knows the success code and the `422` it generates from a request body. It does
**not** know about a `403`, a `404` or a `409` a handler raises with `HTTPException` — those are
invisible to any check that compares two documents. So the status-drift check verifies the
**concrete** statuses the framework declares (which catches a `201` quietly becoming a `200`), and
the *error* half of the contract is proved by the module suite driving each documented refusal over
HTTP. The docstring says all of this, because a reader who assumes the drift test proves the 404s
would be wrong in a way that matters.

**A wildcard is not a promise.** This application's global error handlers put `4XX` and `5XX` on
every operation in the generated document. Requiring the literal `"5XX"` in each of 45 operations
would make the contract say *less*, not more — `404` and `409` are the useful facts. The check skips
wildcards, with the reason written in `_undocumented_statuses`.

**One exclusion, named, with a reason, and checked.** `/api/v1/sites/{site_id}/audit/events` sits
under this prefix but belongs to the audit module (Issue 20). `Contract.excluded` carries it with
that sentence, and a test asserts every exclusion names a route the application *actually serves*
and carries a non-empty reason — so a stale exclusion is a failure rather than dead weight, and the
list cannot quietly become a way of making the suite go green.

**The module suite reads the contract.** `test_every_route_that_names_a_clinic_answers_404_for_another_ones`
takes its paths **from `sites.yaml`**, so a route added to the contract without the site guard's
behaviour fails there. That is what makes this an independent re-verification rather than a second
copy of the per-issue tests.

**`jsonschema` resolves the contract's own refs by bundling, not by a registry.** OpenAPI 3.1's
schemas are JSON Schema 2020-12, and a `$ref` in one points at `#/components/schemas/...` — a
pointer *relative to the document*. A validator built on a bare sub-schema has that sub-schema as
its resource root, so the pointer resolves to nowhere. Carrying `components` alongside the schema
makes the bundle the root, which is what the pointer was written against.

**A correction to the contract, found by the drift test itself.** `DELETE
/sites/{site_id}/staff/invitations/{invitation_id}` returns the withdrawn invitation, not `204`;
the first draft of the contract said `204` because that is what a delete usually does. The
status-drift check caught it in the first run.

**Out of scope:** the contracts for discovery, queue, notifications, channels and reporting. Each is
its own milestone's, and each is one line in `CONTRACTS` plus a YAML file.

## Changes

- **`contracts/sites.yaml`** (new): 45 operations, 54 schemas, 31 paths.
  **`contracts/README.md`** (new): the rules and how to add the next one.
- **`tests/integration/contracts/test_openapi_contracts.py`** (new): the harness — 10 tests, three
  of which are fixtures proving the checks can fail.
- **`tests/integration/sites/test_sites_module.py`** (new): 8 cases — the lifecycle, every
  documented `401`, `403`, `404`, `409` and `422`, and a read of the contract asserting every
  site-scoped operation promises a `404`.
- **`requirements.txt`:** `jsonschema==4.26.0`, with the reason beside it.
- **`docs/GITHUB/MILESTONES/M4_clinics_queues_config.md`:** the exit criteria ticked, with the
  partial written out. **`docs/GITHUB/README.md`** and the milestone header: `make
  milestone-progress` (the bar is generated from GitHub, never typed).

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (208 files).
- [x] `make test`: **1429 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**.
- [x] **The harness was tested with the mistake it exists to catch.** A route added to the sites
      router without touching the contract:

```text
  E  AssertionError: sites.yaml does not document these routes, which the application serves:
  E      GET /api/v1/sites/undocumented
  E  AssertionError: sites.yaml omits these status codes the application can return:
  E      GET /api/v1/sites/undocumented: ['200']
```

      Both checks fire, and both name it. Removing the route makes the suite green again. Three
      more fixtures inside the file prove the other direction, the example check and the
      exclusion-list rule can each fail.
- [x] **The contract matches the application exactly**, right now: 45 documented operations, 45
      served (46 under `/api/v1/sites` minus the one excluded audit route), no drift in either
      direction.
- [x] **Every example validates.** 20+ examples across requests, responses and schemas, each
      checked against the schema it is an example of with the contract's own `$ref`s resolved.
- [ ] **C and B confirm they can build against the contract alone.** *Not done — this is the one
      acceptance criterion this PR cannot satisfy on its own*, because it is two other people's
      sign-off. What is ready for them: `contracts/sites.yaml` describes every route with request
      and response schemas, examples, and the refusals each can produce; `contracts/README.md` says
      what the document guarantees; and the drift test means it cannot rot between now and their
      reading it. Asked for in review.

## Acceptance criteria

- [x] **`sites.yaml` documents every sites and queues route, enforced by the drift test.** 45 of
      45, with the one exclusion named and its reason checked.
- [x] **Adding an undocumented route fails the test suite.** Demonstrated above, with the output.
- [x] **Error responses are documented, not only success responses.** `401`, `403`, `404`, `409`,
      `422` and `503` where the handler can produce them — and each is *driven over HTTP* by the
      module suite, because a document-to-document check cannot see them.
- [x] **Module tests cover the full site lifecycle including onboarding and suspension.** Submitted
      through the public form, verified, configured, and suspended, in one test; plus every read the
      contract promises on a configured clinic.
- [x] **The contract's examples are valid against its own schemas.** Checked with `jsonschema`, and
      a fixture proves the check fails on an example its schema refuses.
- [ ] **Frontend and channel teams confirm they can build against the contract without reading the
      code.** Outstanding: C and B's sign-off, requested in this PR. Everything they need to give
      it is in `contracts/`.

## Risk and rollback

No migration and no runtime change: this PR adds a document, a test suite and one test-time
dependency. The only way it can break a deployment is by failing CI, which is what it is for.

**Follow-ups noticed:** the contract is not published anywhere a consumer can fetch it (the obvious
home is a `/contracts/sites.yaml` static route or a release artefact, and it belongs with the
frontend's own setup rather than here); and `test_sites_module.py` asserts the *statuses* of the
documented refusals but not their bodies, so a `detail` sentence could change without failing
anything — worth a body assertion for the handful a channel adapter actually shows a patient.

Closes #30
