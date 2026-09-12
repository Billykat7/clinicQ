# PR: Generate .env.example from Settings and refuse unsafe configuration in one pass (Issue 12 / M2-12)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#12](https://github.com/Billykat7/clinicQ/issues/12)

A deploy with a bad setting used to fail on the first guard it met, and a deploy with some mistakes
(debug mode on, CORS open to every site) did not fail at all, because the app had no such
settings. This PR moves every configuration rule into one method, `Settings.configuration_problems()`.
The boot guard raises once with the whole list, and `scripts/check_config.py` prints the same list
for an env file without starting anything. `.env.example` is now generated from the `Settings`
class, all 105 settings of it, with a test that fails when the two disagree. Checking the history,
and not only the tree, found one real credential committed since Issue 1. It is removed from the
tree and now caught by a scanner rule, but it **still has to be rotated** (see *Risk*).

## Summary

- **One validation path.** The four separate guards (default JWT secret, weak bcrypt, Stripe,
  Paystack) became entries in `configuration_problems()`, with their messages unchanged, and three
  rules joined them: `DEBUG` and `CORS_ORIGINS=*` are refused outside development, and so are
  `DB_HOST`/`DB_NAME` values that contradict `DATABASE_URL`. `refuse_unsafe_configuration` raises
  one error naming every problem, and never prints the values (`hide_input_in_errors`).
- **Two new settings, wired in.** `DEBUG` drives FastAPI's `debug`; `CORS_ORIGINS` (comma list or
  JSON) installs `CORSMiddleware` for exactly those origins, with credentials, and nothing when
  empty. The development-only `*` never offers credentials.
- **`DB_*` parts no longer ignored in silence.** PR #110 flagged it and left it for this issue: with
  `DATABASE_URL` unset, its default still won, so `DB_HOST` and `DB_NAME` alone sent the app to
  localhost. They now build the URL; when both are given, `DATABASE_URL` wins and a contradicting
  part is refused outside development (a warning locally).
- **`.env.example` from the class.** `scripts/generate_env_example.py` (`make env-example`) writes
  each setting with its description and its default on a commented `# NAME=value` line. Only the
  two compose URLs are active, so a copied `.env` keeps following the code's defaults.
- **`scripts/check_config.py`** (`make check-config`) reads one env file (the shell's environment is
  ignored), lists every unreadable value and every rule it breaks in one run, warns about keys that
  are not settings, prints names but never values, and exits 1 if the app would refuse the file.
- **`docs/CICD/ENVIRONMENTS.md`:** the local, staging and production matrix, the rules, where each
  value lives, and the history record.

## Design notes

**Extend the guard, do not add a second one.** A second validation path (say, a separate checker
in the script) would drift from the boot guard the day someone added a rule to only one of them.
So the rules are data, a list of `ConfigProblem(setting, message)`, and both callers read the same
list. `check_config.py` uses a subclass whose guard collects rather than raises
(`raise_on_configuration_problems = False`) and whose only settings source is the file.

**Every problem in one pass, including unreadable ones.** pydantic runs the model rules only when
every field parsed. So the script records all field errors (`BCRYPT_ROUNDS=99`, `OTP_LENGTH=six`),
drops those values back to their defaults, builds the settings again and adds the rule problems.
One run reports both kinds.

**Commented defaults, not a copy of every default.** A `.env.example` with 105 active lines would
pin today's defaults into every laptop's `.env` (and `VERSION=0.1.0` into a host's, overriding the
version Issue 10 bakes into the image). Commented lines document the default without taking it
over. The drift test counts both forms, and a guard in the generator stops a description line from
ever looking like `# NAME=`.

**What counts as a secret leak, and what was found.** The acceptance criterion is about history, so
three checks ran against every ref, not the tree:

1. gitleaks over every commit: clean against the reviewed baseline;
2. every env file ever added: only the two `*.example` templates;
3. the real values in this machine's untracked `.env`, `.env.dev`, `.env.prod` and `.env.prod.current`
   (44 distinct secret-looking values), searched in all 975 blobs of history.

Check 3 found the shared local platform database password, written as the fallback of
`DB_PASSWORD` in `scripts/run-local-platform.sh` by the Issue 1 scaffold. The same value is
`DB_PASSWORD` in the maintainer's `.env.prod`. gitleaks's default rules do not recognise a shell
default, `${NAME:-value}`, so a new rule, `shell-default-secret`, now does. Run over history, it
found that password plus two development placeholders from the same commit (a `SECRET_KEY` default
long since removed from the compose file, and the local gateway's `REGISTRY_DEPLOY_TOKEN`; neither
matches any real value). All three are baselined as reviewed, so the history scan stays green and
the rule guards every new commit. The literal `REDACTED` is allowlisted, because it is how the
baseline file spells a redacted secret.

**Out of scope:** the deploy workflow and creating the GitHub Environments with their secrets
(Issue 11: the page names the environments and what goes in them), and rotation procedures
(Issue 98).

## Changes

- **`src/core/config.py`:** `DEBUG`, `CORS_ORIGINS` (with a comma/JSON parser), a parse check on
  `DATABASE_URL`, `ConfigProblem`, `configuration_problems()` and `refuse_unsafe_configuration`
  replacing four validators, the `DB_*` build and conflict fix, `ge=1` on
  `SESSION_ABSOLUTE_MAX_DAYS` instead of a validator, `hide_input_in_errors`, and
  `setting_env_names()`.
- **`src/main.py`:** `debug=cfg.debug`; `CORSMiddleware` when origins are named.
- **`scripts/check_config.py`**, **`scripts/generate_env_example.py`** (new); **`Makefile`:**
  `make check-config`, `make env-example`.
- **`.env.example`:** regenerated: 105 settings, 2 active.
- **`tests/unit/platform/test_env_example.py`** (new): the drift test, generator equality, a copy boots
  as development, the example is refused as production (its only secret is the development one),
  and no filled-in env file is tracked.
- **`tests/unit/security/test_config_guards.py`** (new): each new rule, all problems in one error,
  no values in a refused boot, the `DB_*` build and conflict, `check_config` in one pass without
  values, ignoring the shell environment, and exit 2 for a missing file.
- **`tests/integration/platform/test_cors_and_debug.py`** (new): no CORS headers by default, exactly
  the named origin with credentials, the wildcard without them, and `DEBUG` reaching FastAPI.
- **`tests/integration/database/test_seed_dev_data.py`:** runs the seed from an empty directory, so a
  developer's `.env` (`DEBUG=true`) cannot trip the new guard before the seed's own refusal.
- **`scripts/run-local-platform.sh`:** `DB_PASSWORD` has no fallback; it comes from `.env`.
- **`.gitleaks.toml`**, **`.gitleaks-baseline.json`:** the `shell-default-secret` rule, the `REDACTED`
  allowlist entry, and the three reviewed historical matches (all redacted).
- **`docs/CICD/ENVIRONMENTS.md`** (new); **`README.md`**, **`CONTRIBUTING.md`**, **`scripts/README.md`:**
  point to it and to the two make targets.

## Testing

- [x] **`./scripts/ci-local.sh --skip-trivy`** green (quality, pip-audit, secrets, tests, coverage,
      docker) in 104 s; the suite **979 passed, 16 skipped, 11 xfailed** (was 957 passed).
- [x] **`cp .env.example .env` and `make run`** in a clean worktree with no `.env` of its own:

      ```text
      INFO:     Application startup complete.
      GET /health -> 200 {"status":"ok","version":"0.1.0"}
      ```

- [x] **`ENVIRONMENT=production` with the default secret** (same worktree): uvicorn exits 1 with

      ```text
      Value error, refusing to start with this configuration: JWT_SECRET must be set to a secure
      value outside development. Do not use the default secret. [type=value_error]
      ```

      and with `DEBUG=true CORS_ORIGINS='*'` added, the same single error lists all three. No
      `input_value` dump follows it.
- [x] **`scripts/check_config.py` on a broken file**, every problem in one pass, exit 1, no value
      printed (the file held a password and a live key):

      ```text
      check_config: broken.env (as production)
        ✗ BCRYPT_ROUNDS: Input should be less than or equal to 15
        ✗ OTP_LENGTH: Input should be a valid integer, unable to parse string as an integer
        ✗ STRIPE_SECRET_KEY: STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must be set when STRIPE_ENABLED is true.
        ✗ JWT_SECRET: JWT_SECRET must be set to a secure value outside development. Do not use the default secret.
        ✗ DEBUG: DEBUG must be false outside development: debug mode sends tracebacks to whoever triggered the error.
        ✗ CORS_ORIGINS: CORS_ORIGINS must list exact origins outside development, never '*', which lets every website call the app.
        ✗ DB_HOST: DB_HOST disagrees with DATABASE_URL, which wins, so DB_HOST is ignored. Set DATABASE_URL alone, or the DB_* parts without it.
        ! 1 key(s): not settings the app reads (typos, or compose and deploy keys?): IMAGE
      7 problem(s), 1 warning(s): the app would refuse to start with this file.
      ```

      `.env.example` passes as development (exit 0) and, checked `--environment production`, fails on
      `JWT_SECRET` alone. A missing file exits 2.
- [x] **The history checks** (details under *Design notes*): gitleaks `--log-opts=--all` "no leaks
      found" before and after the new rule and baseline; env files ever added: `.env.example`,
      `scripts/cd/setup.env.example`; 44 local secret values against 975 blobs: one real match (the
      platform database password, 2 file versions) and one false positive (`REFRESH_TOKEN_COOKIE_NAME`,
      a cookie name). After the fix, the value is in no tracked file.
- [x] **The seed test** that my `.env` broke passes from an empty working directory: 6 passed
      against the local PostGIS.
- [x] **CI on this PR** (run on `ed250a6`…): every job green in 2 min 17 s, including gitleaks over
      the whole history with the new rule and baseline.
- [ ] **Not done:** a whole-team review of the matrix (a criterion; see below).

## Acceptance criteria

- [x] The app refuses to start in production with the development `JWT_SECRET` (uvicorn exit 1,
      message above; `test_settings_reject_default_jwt_secret_in_production`).
- [x] Debug mode cannot be enabled in staging or production (`test_debug_mode_cannot_be_enabled_outside_development`,
      both environments; the boot demo).
- [x] `.env.example` lists every setting the app reads, checked by a test against `Settings`
      (`test_every_setting_is_in_env_example_and_nothing_else_is`, 105 settings).
- [ ] **No secret value appears anywhere in the repository history: not met yet.** One does: the
      shared local platform database password, in `scripts/run-local-platform.sh` since Issue 1
      (`5001133`). It is removed from the tree, the scanner now catches that shape, and nothing
      else was found. The criterion is met once the password is rotated, which makes the value in
      history dead. It has to be rotated by the owner of that database, and wherever else the same
      value is used.
- [x] `scripts/check_config.py` reports every missing or invalid value in one pass (7 problems in one
      run above; `test_check_config_lists_every_problem_in_one_pass_without_a_value`).
- [ ] The environment matrix is documented (`docs/CICD/ENVIRONMENTS.md`) **but not yet reviewed by
      the whole team**: that needs the team, at the next sprint review.

## Risk and rollback

**Behaviour changes a deployment could notice.** Outside development, the app now refuses to start
with `DEBUG=true`, `CORS_ORIGINS=*`, or a `DB_HOST`/`DB_NAME` that contradicts `DATABASE_URL`, and it
reports every such problem at once. No deployment exists yet (Issue 11), so none breaks today; run
`make check-config ENV_FILE=<host .env> ARGS='--environment production'` before the first one. With
`DATABASE_URL` unset, `DB_HOST` and `DB_NAME` now take effect where they were silently ignored. The
CORS middleware is added only when `CORS_ORIGINS` is set, so the default app is unchanged.

**The leaked password.** It was in a public repository's history before this PR, so treat it as
known: rotate it on the shared platform database, and anywhere the same value is reused. Deleting
it from history was deliberately not done: it would rewrite every commit on `main` (a force-push
the ruleset forbids), break every clone and merged PR, and would not un-publish a value already
public. After rotating, nothing more is needed; the baseline entry documents the old finding.

Rollback is a revert of this PR. The `run-local-platform.sh` change should stay regardless: a new
fallback there would be the same leak again.

Closes #12
