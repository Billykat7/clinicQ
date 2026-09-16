# Deployment environments

The GitHub Environments `deploy.yml` deploys into (Issue 11), kept as code like the rulesets.
`make gh-sync-environments` (`scripts/gh_sync_environments.py`) applies them; `ARGS=--dry-run`
shows the difference first. Secrets and variables are not in these files: they are set in the
Environment on GitHub (docs/CICD/RUNBOOK_DEPLOY.md lists them).

| File | Required reviewers | Deploys only from | Why |
|------|--------------------|-------------------|-----|
| `staging.json` | nobody | workflows running on `main` | a version is tried here first, and nobody has to be waited for |
| `production.json` | the DevOps/QA Lead (`reviewers`, GitHub logins) | workflows running on `main` | a clinic depends on it: one named person approves every deploy |

"Deploys only from `main`" means a workflow edited on a branch can never reach an environment's
secrets: `deploy.yml` only ever runs from `main`, and only as a manual run (`workflow_dispatch`).

GitHub matches environment names without regard to case, so `production` is the environment that
was created as `PRODUCTION` before Issue 11; its existing secrets are left as they were.
