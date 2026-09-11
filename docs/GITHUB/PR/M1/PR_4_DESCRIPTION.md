# PR: Shared kernel: ClinicQ enums, SAST time helpers, UUIDv7 ids and one error envelope (Issue 4 / M1-04)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#4](https://github.com/Billykat7/clinicQ/issues/4)

This branch gives the six of us one vocabulary before anyone writes a model: the ClinicQ enums, one
way to take "now" in Africa/Johannesburg, identifiers that sort by creation time, and one error body
for every API failure. The part that matters most is the conventions guard. Non-negotiable 5 in
`docs/guideline.md` said a naive `datetime.now()` "fails lint", but nothing failed on a magic status
string, and nothing at all enforced "UTC only where a standard requires it". The guard now does all
three, names the file and line, and was shown failing on both violations the issue asks about (see
*Testing*). On its first run it also found five UTC timestamps a grep had missed, because they came
in through aliased imports.

## Summary

- **Enums, beside the kernel's** in `src/commons/enums.py`: `SiteSector`, `TicketStatus`,
  `TicketSource`, `DisplayMode`, plus `TICKET_TERMINAL_STATUSES` and
  `SITE_DEFAULT_DISPLAY_MODE = number_only`. **`UserRole` is extended** with ClinicQ's five roles
  instead of adding a second `StaffRole`.
- **Time helpers** in `src/commons/time.py`: `now_sast()`, `to_sast()`, `business_date()` and
  `business_day_bounds()`. `APP_TIMEZONE` is now defined once there; `src.core.s3_logging`
  re-exports it, and the copy in `src/web/routes.py` is gone.
- **Identifiers** in `src/commons/ids.py`: `new_id()` returns a UUIDv7 string (Python 3.14's
  `uuid.uuid7()`), in the same `String(36)` column the kernel uses. The `Widget` example model, which
  new modules copy, now uses it.
- **The error envelope, extended rather than forked.** `src/commons/exceptions.py` gains
  `ErrorEnvelope` and six category bases that each carry an HTTP status. The new
  `src/core/error_handlers.py` answers domain errors, `HTTPException`, validation errors and
  unhandled 500s with one body: `{detail, code, request_id}`.
- **Documented in OpenAPI:** every `/api/v1` operation (132 of 132) documents `ErrorEnvelope` for
  `4XX` and `5XX`. The new public `GET /api/v1/reference/enums` makes each enum a named schema with
  its allowed values.
- **The guard,** `tests/unit/commons/test_conventions.py`, covers naive datetimes, UTC outside the
  modules a standard obliges to use it, and magic status strings. Its 52 must-catch and must-allow
  cases show that it can fail.

## Design notes

**Extend, don't fork, on the wire as well.** The kernel already answers errors as `{"detail": …}`,
and five static JS files read `detail` (a string, or FastAPI's list of field errors). The envelope
keeps `detail` exactly as it was and adds `code` and `request_id`, so no client changes and no
existing test changed. The RBAC 403 already carries a structured `detail` with its own code
(`insufficient_permission`); the handler promotes that code to the top level rather than inventing a
second one. `HTTPException` headers (`WWW-Authenticate`, `Location`) pass through untouched.

**The status belongs to the error's category, not to each route.** A service raises
`TicketNotFoundError(NotFoundError)` and knows nothing about HTTP, because a USSD session and a
scheduled job call the same code. The handler reads `status_code` off the category (404, 409, 422,
403, 502, 503; 400 for the bare base). The 16 kernel exceptions that are actually raised today are
reparented onto the category their router already maps them to; each mapping was checked against its
`except` block, and all 16 matched. Those routers keep their `try`/`except`, so behaviour is
unchanged; the handler is the net for anything a route does not catch. The base keeps its name,
`BKPropertyError`: renaming it would touch 60 subclasses for no behaviour.

**One role vocabulary.** Issue 18 replaces the property project's portal roles with ClinicQ's. This
PR adds `patient`, `receptionist`, `nurse_doctor`, `clinic_manager` and `platform_admin` to
`UserRole` and nothing else. Nothing seeds from the enum (`default_system_roles()` lists only `user`
and `admin`), so adding members grants nobody anything. Retiring `tenant`/`owner`/`manager`/`vendor`
stays with Issue 18, together with the RBAC tests that still name them.

**`TicketStatus` follows the specs that consume it:** `waiting`, `called`, `recalled`, `in_progress`,
`done`, `no_show`, `cancelled`, `transferred` (Issues 41, 43 and 45). `recalled` is Issue 43's
"recall state", which the product doc also names. `TicketSource` uses `walk_in`, the value Issue 51
passes. The transition table stays with Issue 41; only the terminal set is defined here, so the
board, notifications and reports agree on what "finished" means. `NotificationChannel` already
existed (`email`, `sms`); the channels M9 adds belong to those issues.

**Strict time helpers.** `to_sast()` and `business_date()` refuse a naive datetime with a
`ValueError` rather than guessing UTC or the server's zone, because that guess is the bug. The
service day turns at SAST midnight (22:00 UTC), and `business_day_bounds()` gives the half-open
`[start, end)` to filter `timestamptz` columns by. Storage needs no helper: PostgreSQL stores a
`timestamptz` as UTC whatever offset it arrives with.

**UUIDv7, same column.** `uuid.uuid7()` puts a millisecond Unix timestamp first and a 42-bit counter
after it, so ids made in the same millisecond still sort (checked with 100,000 in a tight loop).
Canonical UUIDs are lowercase hex and hyphens: RFC 3986 unreserved, so URL-safe without escaping.
They stay 36-character strings, so no migration is needed and existing UUIDv4 rows coexist. The
timestamp is readable by anyone holding the id (`id_created_at()`), which is fine for tickets and
sites, and is why the module says never to use one as a secret.

**How the guard works.** It parses `src/` and `scripts/db/` with `ast`. It resolves imports first,
so `import datetime as dt; dt.datetime.now()` and `from datetime import datetime as DateTime` are
still caught. There are three rules:

1. *Naive datetimes:* `now()` or `fromtimestamp()` without a zone, `today()`, `utcnow()`,
   `utcfromtimestamp()`, `date.today()`, `combine()` without `tzinfo`, `strptime()` without `%z`,
   and the constructor without `tzinfo`.
2. *UTC "now"* (`UTC`, `timezone.utc`, `ZoneInfo("UTC")`), allowed only in the modules listed in
   `UTC_REQUIRED_BY_A_STANDARD`, each entry naming its standard (RFC 7519 for JWTs, RFC 5280 for a
   certificate's `notAfter`). An entry that stops using UTC fails the build too, so the list cannot
   quietly turn into a blanket exemption.
3. *Magic strings:* a literal compared with, assigned to, passed as, defaulted into, stored under or
   `match`ed against a status-like name (`status`, `*_status`, `role`, `source`, `sector`,
   `display_mode`, `channel`, `state`), or compared with anything when it spells a ClinicQ wire
   value such as `"no_show"`.

Migrations are out of scope on purpose, because a migration freezes the literals of its day. There
is no inline escape hatch: per the guideline, breaking the rule is a team discussion.

**What the guard found on its first run.** `rbac_admin.py` stamped two grant `created_at` values in
UTC, beside a `granted_at` in the same file stamped in SAST, and the alerts clock used UTC where the
messaging clock uses SAST. All three are business timestamps, so all three now use SAST; it is the
same instant in `timestamptz`. `download_links.py` mints a JWT, so it was allowlisted. The
auth/session cluster (`auth.py`, `refresh_token_policy.py`, `verification.py`, and the resend in
`rbac_admin.py`) stays UTC as a unit. Those values are compared with JWT `exp`/`iat`, and on SQLite
they are read back naive and treated as UTC, so moving one of them alone would shift a cooldown by
two hours in the tests. Issue 15 rebuilds that model, including `last_login`. Two `role="admin"`
literals in RBAC manifests became `UserRole.ADMIN.value`.

**Out of scope:** using the enums in models (Issues 23, 27, 39), the state machine (Issue 41), and
switching existing kernel models from UUIDv4 (no behaviour behind it).

## Changes

- **`src/commons/enums.py`:** the four ClinicQ enums, the terminal-status set, the default display
  mode, and five roles added to `UserRole`.
- **`src/commons/time.py`** (new): `APP_TIMEZONE`, `now_sast()`, `to_sast()`, `business_date()`,
  `business_day_bounds()`.
- **`src/commons/ids.py`** (new): `new_id()`, `id_created_at()`.
- **`src/commons/exceptions.py`:** `ErrorEnvelope`, `status_code` and `to_envelope()` on the base,
  six category bases, and the 16 raised kernel exceptions reparented onto their categories.
- **`src/core/error_handlers.py`** (new) and **`src/main.py`:** the four handlers, and
  `ERROR_RESPONSES` on the `/api/v1` router.
- **`src/api/v1/routes/reference.py`** (new) and **`src/api/v1/router.py`:**
  `GET /api/v1/reference/enums`.
- **`src/core/s3_logging.py`**, **`src/web/routes.py`:** import `APP_TIMEZONE` instead of defining
  it.
- **`src/api/v1/routes/rbac_admin.py`**, **`src/modules/alerts/service.py`:** business timestamps in
  SAST.
- **`src/modules/{widgets,communications}/rbac_manifest.py`:** `UserRole.ADMIN.value`.
- **`src/database/models/widget.py`:** `default=new_id`, and the docstring convention updated.
- **Tests (new):** `tests/unit/commons/test_conventions.py` (the guard and its self-tests),
  `test_time.py`, `test_ids.py`, and `tests/integration/platform/test_error_envelope.py` (real
  requests through `create_app()`).
- **Docs:** `docs/guideline.md` (non-negotiable 5 names the guard's real path and what it checks),
  `docs/GITHUB/ISSUES/README.md` (the shared-code row), and the Issue 4 spec (where `APP_TIMEZONE`
  really lived; files touched).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (157 files, 4 new)
- [x] `make test`: **775 passed** (682 on `main` plus 93 new). 24 fail (13 failures, 11 errors),
      exactly the 24 that fail on `main`, compared test by test with `diff` (the workflow,
      `.gitleaks.toml` and `docs/SECURITY/` guards that Issues 7 and 9 and the security issues clear)
- [x] **The guard fails on a naive `datetime.now()` and on a magic status string.** Four lines were
      appended to a real module, `src/modules/widgets/service.py`:

      ```python
      def _issue4_demo_archive(widget: Widget) -> None:
          from datetime import datetime
          widget.archived_at = datetime.now()
          if widget.status == "archived":
              return
      ```

      ```text
      $ pytest tests/unit/commons/test_conventions.py -k anywhere_in_the_tree
      E  AssertionError: Naive datetimes (business time is Africa/Johannesburg: use src.commons.time):
      E    src/modules/widgets/service.py:115: naive datetime.now(): pass a zone, or use now_sast()
      E  AssertionError: Magic strings (import the enum from src.commons.enums):
      E    src/modules/widgets/service.py:116: magic string 'archived' compared with status: use the enum member
      2 failed, 55 deselected
      ```

      With the lines removed: `57 passed`.
- [x] The guard's own cases: 12 naive forms, 6 UTC spellings and 18 magic-string shapes (compare,
      `in`, subscript, assignment, keyword, dict payload, parameter default, `match`) are each
      reported; 7 aware forms and 9 near misses (`open(path, mode="rb")`, `status_code = 404`,
      `"waiting room"`, enum members) are not
- [x] OpenAPI, generated by the app:

      ```text
      SiteSector    ['public', 'private']
      TicketStatus  ['waiting', 'called', 'recalled', 'in_progress', 'done', 'no_show', 'cancelled', 'transferred']
      TicketSource  ['web', 'ussd', 'whatsapp', 'walk_in']
      DisplayMode   ['number_only', 'name_lite', 'full']
      UserRole      ['user', 'admin', 'patient', 'receptionist', 'nurse_doctor', 'clinic_manager', 'platform_admin', 'tenant', 'owner', 'manager', 'vendor']
      ErrorEnvelope required: ['detail', 'code']  props: ['detail', 'code', 'request_id']
      api/v1 operations documenting ErrorEnvelope on 4XX: 132 of 132
      ```

- [x] The envelope over real requests: a domain error of each category raised from a stand-in
      service below a route that maps nothing answers 404, 409, 422, 403, 502, 503 or 400 with its
      own code. An unknown path answers `404 {"detail": "Not Found", "code": "http.not_found",
      "request_id": "e74edec6-…"}`. A 401 keeps `WWW-Authenticate`. The RBAC 403 keeps its
      structured detail. A 422 keeps FastAPI's field list. A crash answers a generic 500 whose body
      does not contain the exception's message
- [x] Time and ids: the service day turns at 22:00 UTC; bounds are half-open SAST midnights; naive
      input is refused; 10,000 ids in a loop come out sorted and unique; `quote(id, safe="") == id`
- [ ] Screenshot: no UI change

## Acceptance criteria

- [x] Every status value used on the wire is an enum member, not a bare string (the guard's
      magic-string rule passes on the tree, and fails on a literal status, shown above)
- [x] `now_sast()` returns a timezone-aware datetime in `Africa/Johannesburg` (`tzinfo is
      APP_TIMEZONE`, offset +02:00)
- [x] A domain error raised in a service becomes a documented JSON error body with the right status
      (seven categories over real requests; `ErrorEnvelope` on 132 of 132 operations)
- [x] The guard test fails when a naive `datetime.now()` is introduced, and names the file (shown
      above)
- [x] Identifiers are URL-safe and monotonically sortable (UUIDv7; 10,000-id ordering and
      percent-encoding tests)
- [x] Enums are documented in the OpenAPI schema with their allowed values (five named schemas,
      listed above)

## Risk and rollback

Additive for clients: every error body keeps its `detail` and gains two fields, and no existing test
changed. A route that let a domain error escape used to answer a plain-text 500; it now answers the
category's status. That is the intended fix, and no kernel route relies on the old behaviour. Three
timestamps change from UTC to SAST, which is the same instant in PostgreSQL. No migration. Rollback
is a revert of this PR.

**Follow-ups found along the way:**

- The `request_id` of an unhandled 500 is `null`, because the request-logging middleware clears its
  context before Starlette's outermost handler runs. Issue 6, which puts the id in a response header,
  is the place to keep it alive for that path.
- About 45 exceptions in `src/commons/exceptions.py` (leases, tenancies, work orders, inspections)
  are raised nowhere. They are residue of the property project and are worth deleting in their own
  PR.
- `last_login` is written in UTC by `auth.py`, the very example the timezone rule forbids; Issue 15
  owns it.
- The `.cursor/rules` files are ignored by git here (a global ignore on `.cursor/`), so the local
  timezone and magic-string rules were updated to point at `src.commons.time` and the guard. The
  tracked `docs/guideline.md` carries the same convention for everyone else.

Closes #4
