# API contracts

One hand-written OpenAPI document per consumed surface, so the frontend and the channel adapters can
be built against a stable description instead of against whatever the code happens to return this
week.

| Contract | Covers | Issue |
|---|---|---|
| [`sites.yaml`](sites.yaml) | Clinics, hours, queues, services, display settings, staff, onboarding, payment profile, analytics switch and conversion report | [30](../docs/GITHUB/ISSUES/M4/ISSUE_30_sites_openapi_contract_tests.md) |
| [`discovery.yaml`](discovery.yaml) | The nearby search, place-name typeahead, recent areas, one clinic's profile, the search rate limit | [38](../docs/GITHUB/ISSUES/M5/ISSUE_38_discovery_analytics_contract.md) |

Queue (47), notifications (71), channels (79) and reporting (94) each add their own
file here and one `Contract(...)` entry to `tests/integration/contracts/test_openapi_contracts.py`.
Nothing else about the harness changes.

## The rules

1. **Hand-written, not generated.** A generated document describes the code; the point of a contract
   is to be the thing the code has to match, which means somebody has to decide what it says.
2. **Checked in both directions.** An undocumented route fails the suite, and so does a documented
   path with no handler. `make test` runs it.
3. **Error responses, not only the happy path.** `400`/`403`/`404`/`409`/`422` are documented where
   the handler can produce them, and the module suite
   (`tests/integration/sites/test_sites_module.py`) drives each one over real HTTP — FastAPI's own
   document does not know about a `404` a handler raises, so the drift test alone cannot prove it.
4. **Every example validates against its own schema.** An example is the part a consumer copies, so
   one the API would refuse is a trap.
5. **A wildcard is not a promise.** `4XX` / `5XX` are what this application's global error handlers
   put on every operation. The drift test skips them and checks the concrete codes, which are the
   useful facts.

## Adding a contract

```python
# tests/integration/contracts/test_openapi_contracts.py
CONTRACTS = (
    ...,
    Contract(name="discovery", filename="discovery.yaml", prefix="/api/v1/clinics"),
)
```

Every test in that file is parameterised over `CONTRACTS`, so that one line is the whole wiring.
A route under your prefix that belongs to another module's contract goes in `excluded`, **with the
reason**: the suite fails on an exclusion that names a route the application does not serve, or one
with no reason, so the list stays a set of decisions somebody can read.
