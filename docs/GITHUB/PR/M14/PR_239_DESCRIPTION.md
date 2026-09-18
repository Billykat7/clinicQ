# PR: CD secrets that can be reviewed, and a repository that does not name the host (Issue 239 / M14-239)

**Milestone:** [Milestone 14: Production Readiness, Pilot & Go-live](https://github.com/Billykat7/clinicQ/milestone/14) ·
**Issue:** [#239](https://github.com/Billykat7/clinicQ/issues/239) · **Builds on:** #234 (Issue 230,
the deploy on the host's own runner), merged

Two things were true of this repository, which is public:

```yaml
# .github/workflows/deploy.yml
dir="${dir:-/opt/btk/clinicq$suffix}"
```

```yaml
# the app's whole .env, as one GitHub Environment secret
APP_ENV: ${{ secrets.APP_ENV }}
```

The first published the layout of the machine the app runs on. The second put 238 keys into a box
that cannot be read back, diffed, or versioned — and that GitHub caps at 48 KB per secret and 64 KB
per repository.

## The size limit was the symptom, not the disease

Worth being precise, because it changes what the fix has to be. The `.env` is 35 KB on disk but only
**8.8 KB of actual `KEY=value`** — the rest is comments. So `APP_ENV` was not literally about to
fail. What was actually wrong is that GitHub secrets are **write-only**:

| | GitHub Environment secret | what a deployment needs |
|---|---|---|
| Read a value back to check it | no | yes |
| Diff what changed, and when | no | yes |
| Audit who read it | no | yes |
| Review a change before it ships | no | yes |
| Roll one back | no | yes |

*"What was production running last release"* had no answer. That is the problem worth solving, and
solving it happens to remove the size ceiling permanently as well.

## 21 of 182 settings are secret

The split follows the only line that matters:

| | count | where it goes now |
|---|---:|---|
| Settings that hold a credential | **21** | encrypted, SOPS + age, in the private `Billykat7/infra` |
| Settings that do not | **161** | `deploy/env/<environment>.env`, committed here and reviewed |

Feature flags, timeouts, OAuth endpoint URLs and log formats are **decisions**, not credentials. A
decision that changes how production behaves belongs in a pull request with a diff, not pasted into
a web form where nobody can see what moved. Each committed file holds only what that environment
sets *differently from the code's default*, so both are about ten lines.

### Why the encrypted half is not in this repository

It is public. Committed ciphertext is published permanently, so a future compromise of the age key
would read every value this project has ever held, **retroactively**. In a private repository the
ciphertext is a second lock rather than the only one. The age private key stays on the deploy host,
so compromising this repository's GitHub Environments does not reach production's secrets either.

`SOPS_AGE_KEY` remains as an Environment secret for a host that cannot hold its own identity. Not
setting it is the better arrangement, and the workflow says so.

## One classifier, or the split is a fiction

`setting_is_secret()` now lives in `src/core/config.py` beside the settings, shared by
`.env.example`'s generator and by the guard test, so they cannot drift.

It is **type-aware**, which is the part that makes it usable: a credential is a *string*. That is
what separates `JWT_SECRET` from `REFRESH_TOKEN_EXPIRE_DAYS` (an int) and `ACCESS_TOKEN_COOKIE_NAME`
(a name, not a value). On top of that it knows the names whose value hides a credential without
saying so — `DATABASE_URL`, `REDIS_URL`, `SENTRY_DSN`, `SMTP_USER`, `TEAM_WEBHOOK_URL`,
`AWS_ACCESS_KEY_ID`.

Moving it corrected `.env.example` in both directions: **five settings it had missed** (every one of
the credential-bearing URLs above) and **six it had wrongly flagged** (the policy knobs).

## Summary

- `deploy/env/{staging,production}.env` + `deploy/env/README.md` — the non-secret half, committed.
- `scripts/cd/compose-env.sh` — merges both halves into `$DEPLOY_DIR/.env` (mode 600) on the host.
  It builds in a temporary directory and replaces the live file only once whole, so a failure never
  leaves a half-written `.env` the app might start with. It prints counts, never a value.
- `setting_is_secret()` / `secret_setting_names()` in `src/core/config.py`; `.env.example` regenerated.
- `APP_ENV` **removed**. A host still holding its own `.env` keeps deploying, with a warning.
- `DEPLOY_DIR` is the only source of the deploy directory. Unset → "not provisioned", nothing
  changes, the run succeeds.
- `GATEWAY_COMPOSE_DIR` lost its fallback: `${GATEWAY_COMPOSE_DIR:?…}` fails clearly instead of
  mounting a path nobody chose.

### The masking, which is not optional here

Actions logs on a public repository are public, and GitHub masks `secrets` but **not** `vars`. So
`DEPLOY_DIR` as a plain variable would have published the path anyway. Three things together:

- the workflow `::add-mask::`es the value whatever kind it came from;
- the directory is never a step output;
- no step uses a `working-directory:` expression — they `cd "$DEPLOY_DIR"`.

The runbook asks for it as a **secret**, with the variable as a working second choice.

## Verification

**The two guards, shown failing.** Neither is vacuous:

```
$ echo 'DATABASE_URL=postgresql://u:pw@h/db' >> deploy/env/production.env
$ pytest tests/unit/platform/test_deploy_env.py -k no_key_in_the_committed
E  AssertionError: deploy/env/production.env is committed to a public repository and
   names credential-bearing settings: DATABASE_URL

$ echo "# /opt/btk/clinicq" >> scripts/README.md
$ pytest tests/unit/platform/test_workflow_guardrails.py -k absolute_host_path
E  AssertionError: an absolute path on the deploy host is written down in
   scripts/README.md:31 -- use $DEPLOY_DIR (the Environment variable) or $GATEWAY_COMPOSE_DIR instead
```

**The whole chain, against real `sops` and `age`** (both installed; a throwaway identity and four
demo values, none of them real):

```
$ sops --encrypt --age age12t658… --input-type dotenv plain.env > clinicq/production.env
$ head -1 clinicq/production.env
JWT_SECRET=ENC[AES256_GCM,data:4qRXn6BfTIEE…,iv:…,tag:…,type:str]

$ SECRETS_DIR=… SOPS_AGE_KEY_FILE=…/age.key ./scripts/cd/compose-env.sh production out.env
Settings: 10 from deploy/env/production.env (in git) + 4 decrypted = 14 keys.

$ ls -l out.env
-rw-------  out.env

$ python scripts/check_config.py out.env --environment production
  ! 2 key(s): not settings the app reads (typos, or compose and deploy keys?): APP_PORT, DOMAIN
OK: the app would start with this file (1 warning(s)).
```

The composed file is mode 600, carries no `sops_*` metadata, and passes production's own rules. The
two warned keys are the compose file's, which is what `check_config.py` is meant to say about them.

**The suites:**

```
$ TZ=UTC pytest tests/unit/platform/test_deploy_env.py tests/unit/platform/test_workflow_guardrails.py
48 passed, 1 xfailed

$ TZ=UTC pytest tests/unit
1382 passed, 3 xfailed
```

**Not tested, and needing an operator:** no deploy has run with this. The host does not yet have
`sops`, an age identity, or a checkout of `Billykat7/infra`, and `DEPLOY_DIR` is not set on either
Environment — so until those exist, a deploy reports *not provisioned* and changes nothing, which is
the designed behaviour and not a passing test. The runbook's **Secrets → Setting the host up** is
the list.

## Acceptance criteria

- [x] **No tracked file names an absolute path on the deploy host.** `grep -rn "/opt/btk"` over the
      tracked tree returns only the merged pull-request records, which are an account of what
      shipped. The guard test proves itself by failing above.
- [x] **`DEPLOY_DIR` is the only source of the deploy directory.** Nothing is derived or appended;
      unset, the run says what to set and changes nothing.
- [x] **Masked in the log, never a step output.** Asserted in `test_workflow_guardrails.py`,
      including that no step carries a `working-directory:` expression.
- [x] **No key in the committed half is a secret.** Guard shown failing above.
- [x] **Every key there is a setting or a documented compose key.** Separate test.
- [x] **`compose-env.sh` writes mode 600, prints no value, leaves no half-written file.** Shown
      above; the temporary-directory build is what gives the last one.
- [x] **`APP_ENV` is gone and a host on its own `.env` keeps deploying.** Asserted in the workflow
      guard: `compose-env.sh` present, `APP_ENV` absent, host fallback retained.
- [x] **`.env.example` and the guard agree**, because they import the same function.

## Risk and rollback

**The one behaviour change to know about:** `DEPLOY_DIR` is now required. A deploy run before it is
set on the Environment reports *not provisioned* and changes nothing — it does not fail, and nothing
that is serving is touched. That is the same shape the workflow already used for a directory that
does not exist.

`APP_ENV` being removed affects nothing today: neither Environment is provisioned yet, so nothing is
relying on it. A host that does have its own `.env` keeps using it.

Rolling back is `git revert` of the merge; the deploy's behaviour returns with it, and no host state
has been changed by this PR.

**Worth a reviewer's eye:** the age identity's custody. It is the one key that reads every
production secret, it lives in `/etc/btk/secrets/age.key` on the host, and there is no escrow copy —
losing the host loses the ability to decrypt. A copy belongs wherever the team keeps its other
break-glass material.

Closes #239
