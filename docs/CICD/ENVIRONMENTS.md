# Environments: local, staging, production

Three environments run the same image with different settings. This page says what differs and
why, where each value lives, and what the app refuses to start with. The settings themselves are
defined once, in `src/core/config.py`; [`.env.example`](../../.env.example) lists every one of them
with its description and default, and is generated from that class (`make env-example`).

## The matrix

| Setting | Local | Staging | Production | Why it differs |
|---------|-------|---------|------------|----------------|
| `ENVIRONMENT` | `development` (the default) | `staging` | `production` | switches every rule below on |
| `DEBUG` | `true` allowed | **must be `false`** | **must be `false`** | debug mode sends tracebacks to whoever caused the error |
| `CORS_ORIGINS` | empty, or `*` | empty, or the staging origins | empty, or the production origins | `*` lets every website call the app with a visitor's browser |
| `JWT_SECRET` | the development default | its own random secret | its own random secret | a shared or default secret lets anyone mint a session |
| `BCRYPT_ROUNDS` | 4 allowed (fast tests) | 12 or more | 12 or more | the work factor is what makes a stolen hash expensive |
| `DATABASE_URL` | the compose stack (`localhost:5432`) | the staging database | the production database | never shared: staging data is test data |
| `REDIS_URL` | the compose stack (`localhost:6379`) | staging Redis | production Redis | readiness checks the one it names |
| `STRIPE_SECRET_KEY`, `PAYSTACK_SECRET_KEY` | test keys or blank | test keys only (`sk_test_…`) | live keys allowed (`sk_live_…`) | no environment but production can move real money |
| `TRUST_PROXY_HEADERS` | `false` | `true` (behind the gateway's nginx) | `true` | only a real proxy's `X-Forwarded-For` is trustworthy |
| `PUBLIC_BASE_URL` | blank | `https://` staging URL | `https://` production URL | absolute links in email |
| `LOG_FORMAT` | `text` is easier to read | `json` | `json` | the log pipeline parses one object per line |
| `AWS_S3_LOGGING_ENABLED` | `false` | `true` | `true` | only deployed logs are shipped |
| `SENTRY_DSN` | blank (off) | the staging project's DSN | the production project's DSN | errors go to the environment they happened in (Issue 14) |
| `ERROR_TRACKING_TEST_ROUTE` | may be `true` | `true` while proving error tracking | **refused** | a route that fails on purpose has no place in production |
| `METRICS_ENABLED`, `METRICS_TOKEN` | `true`, token optional | `true` **with** a token | `true` **with** a token | `/metrics` tells its reader the app's traffic |
| Development pages (`/dev/...`) | served | not registered (404) | not registered (404) | the component catalogue is for building, not for users |
| `/docs`, `/openapi.json` | served | served | not served | the API surface is not published in production |

Anything not in this table has the same default everywhere and needs no per-environment value.

## What the app refuses to start with

`Settings.configuration_problems()` in `src/core/config.py` is the one list of rules. At boot, the
app raises **one** error listing every problem at once. `scripts/check_config.py` prints the same
list for an env file without starting anything, so neither can drift from the other.

| Rule | Applies in |
|------|-----------|
| `JWT_SECRET` is not the development default | staging, production |
| `BCRYPT_ROUNDS` is 12 or more | staging, production |
| `DEBUG` is false | staging, production |
| `CORS_ORIGINS` does not contain `*` | staging, production |
| `DB_HOST` / `DB_NAME`, when set, agree with `DATABASE_URL` (which wins) | staging, production (a warning locally) |
| an enabled gateway has its keys (`STRIPE_*`, `PAYSTACK_SECRET_KEY`) | everywhere |
| a live payment key (`sk_live_…`) | production only |
| `METRICS_TOKEN` is set while `METRICS_ENABLED` is true (Issue 14) | staging, production |
| `ERROR_TRACKING_TEST_ROUTE` is false (Issue 14) | production |
| every value is readable at all (types, ranges, a parseable `DATABASE_URL`) | everywhere |

A new rule goes into `configuration_problems()`, never into a validator of its own. The guard tests
are `tests/unit/security/test_config_guards.py` and `tests/unit/security/test_security.py`.

`DB_HOST`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` build `DATABASE_URL` when `DATABASE_URL` itself is
not set (or still holds `${...}` placeholders). Until Issue 12, an unset `DATABASE_URL` still won
with its default, so the parts on their own were silently ignored and the app went to `localhost`.
Set one or the other: `DATABASE_URL` alone is simplest.

## Checking a file

```bash
python scripts/check_config.py                                  # ./.env
python scripts/check_config.py .env --environment production     # as if it were production
make check-config ENV_FILE="$DEPLOY_DIR/.env"                     # on the host, before a deploy
```

It reads only the file (an exported shell variable cannot rescue it), prints setting names and
never values, lists every problem in one run, and exits 1 when the app would refuse the file. Keys
that are not settings (compose and deploy keys such as `IMAGE`, or typos) are listed as a warning.

## Where values live

| Where | What | Who can read it |
|-------|------|-----------------|
| `.env.example` (in git) | every setting, with safe defaults and **no real secret** | everyone |
| `.env` on a laptop (git-ignored) | local values; copy of `.env.example` plus anything personal | the developer |
| GitHub Environment `staging` (secrets) | staging's `JWT_SECRET`, `DATABASE_URL`, provider test keys — only when the settings are kept in GitHub rather than in the host's own `.env` (Issue 230) | the deploy workflow (Issue 11) |
| GitHub Environment `production` (secrets, approval required) | production's secrets | the deploy workflow, after the DevOps/QA Lead approves |
| `$DEPLOY_DIR/.env` on each host (mode 600) — `DEPLOY_DIR` is the variable on that GitHub Environment | the running app's settings | the host's deploy user |

Secrets never go in the repository, in any branch, in any form. Three things hold that line:

1. `.gitignore` ignores every `.env*` file except the templates; `tests/unit/platform/test_env_example.py`
   fails if any other env file is ever tracked.
2. The pre-commit hook scans each commit with gitleaks before it exists.
3. CI scans the **whole history** with gitleaks on every pull request (Issue 9).

A secret that reaches a commit is compromised, even if the next commit deletes it: rotate it first,
then remove it, then record the old finding in `.gitleaks-baseline.json` after review.

## History

Issue 12 checked the history, not only the working tree, on 2026-09-11:

- **gitleaks over every commit on every ref:** no leaks against the reviewed baseline.
- **Every env file ever added:** only `.env.example` and `scripts/cd/setup.env.example`.
- **The real values in the developers' untracked env files, searched in every file version on every
  ref (975 blobs):** one was found. The shared local platform database's password had been the
  fallback of `DB_PASSWORD` in `scripts/run-local-platform.sh` since the Issue 1 scaffold
  (`5001133`), and the same value is used in the maintainer's other env files. gitleaks's default
  rules do not recognise a shell default (`${NAME:-value}`), which is why nothing caught it.

What was done: the fallback is removed (the script now requires `DB_PASSWORD` from `.env`); a
gitleaks rule, `shell-default-secret`, now catches that shape in every commit and in CI; and the
three historical matches it finds (this password, plus two development placeholders from Issue 1)
are recorded in the baseline. **Still to do, by the owner of that database: rotate the password.**
The repository is public, so the old value must be treated as known. Rewriting history would not
un-publish it and would break every clone and merged pull request, so it was not done.

## Changing a setting

1. Add or change the `Field` in `src/core/config.py`, with a description (it becomes the comment in
   `.env.example`) and a default that is safe for a laptop.
2. If staging or production must not run with some value, add the rule to
   `configuration_problems()` and a test beside the others.
3. `make env-example`, then commit `.env.example` with the change; the test fails until you do.
4. If the value differs per environment, add a row to the matrix above, and the secret to the
   GitHub Environment and the host's `.env` before the release that needs it.
