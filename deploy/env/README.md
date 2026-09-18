# `deploy/env/` — the settings that are *not* secret

One file per environment, holding only what that environment sets **differently from the code's
default** in `src/core/config.py`. Everything not named here runs on the class default, which is
why these files are short enough to read in a pull request.

They are committed on purpose. Feature flags, timeouts, log formats and public URLs are decisions,
and a decision that changes how production behaves should go through review and leave a trace in
`git log` — not be pasted into a box in the GitHub UI where nobody can diff it.

**No credential is ever in here.** `tests/unit/platform/test_deploy_env.py` fails if any key in
these files is one `src.core.config.setting_is_secret` calls a secret, and gitleaks scans them like
everything else. The ~21 secret settings live encrypted in `Billykat7/infra` (SOPS + age) and are
merged in on the host at deploy time — see [the runbook](../../docs/CICD/RUNBOOK_DEPLOY.md#secrets).

## How a deploy uses them

`scripts/cd/compose-env.sh` writes `$DEPLOY_DIR/.env` (mode 600) as:

```
deploy/env/<environment>.env   +   the decrypted secrets   =   $DEPLOY_DIR/.env
```

Later keys win, so a secret always overrides a same-named key here — which the guard test makes
impossible anyway. The result is checked with the *new image's* own rules
(`scripts/check_config.py`) before anything starts.

## Changing one

1. Edit the file, open a pull request. The diff is the change.
2. CI checks every key is a real setting, is not a secret, and that the file still passes
   `check_config.py` for its environment.
3. Deploy. The next run composes the new file; no box to remember to update.

Adding a **secret** is the other path — the runbook's *Adding or rotating a secret*.
