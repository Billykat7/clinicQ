# Self-hosted GitHub Actions runner as a systemd service

How to install a self-hosted runner so it **starts automatically after reboot** (and after logout).

Do **not** rely on `./run.sh` for anything permanent — that process dies when the SSH session ends.

| Doc | Purpose |
|-----|---------|
| **This file** | Service install, enable-on-boot, verify, day-2 operations, troubleshoot |
| [clinicq.md](./clinicq.md) | ClinicQ install (`~/actions-clinicq`, label `clinicq-deploy`) on a host that already runs another runner |

## Where ClinicQ stands today

**Nothing in this repository needs a self-hosted runner yet.** Every job in `ci.yml`, `release.yml`
and `deploy.yml` runs on `ubuntu-24.04`, and the deploy reaches the host over SSH with the
environment's `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY` / `DEPLOY_KNOWN_HOSTS` secrets
([RUNBOOK_DEPLOY.md](../../CICD/RUNBOOK_DEPLOY.md)). A runner installed by these notes is an
*alternative* to that SSH hop, not an addition to it.

Minutes are not the argument either: **the repository is public, and GitHub does not bill standard
runners for public repositories** — the timing API reports 0 billable milliseconds
([PIPELINES.md](../../CICD/PIPELINES.md#minutes-measured-and-projected-against-2000-a-month)). The
2,000-minute projection there is what the same usage *would* cost if the repository went private.

`scripts/README.md` also records the platform path: production CD for BTK products goes through
infra's `cd-product.yml` on the shared platform runner (`runs-on: [self-hosted, Linux, X64, infra]`),
in which case ClinicQ registers no runner of its own.

## When the team needs one

Add a self-hosted runner when either is true:

- The deploy target is **not reachable from a GitHub-hosted runner** — a VPS behind a firewall, or a
  machine on a home connection that cannot accept an inbound SSH session from GitHub's ranges.
- The repository becomes **private** and deploys are frequent enough that build minutes are a real
  constraint (roughly: more than one release a day, sustained). Self-hosted minutes are never billed.

Otherwise the GitHub-hosted deploy is less machinery to own, and one fewer long-lived credential on a
box the team has to keep patched.

This lands in [Issue 102](../ISSUES/M14/ISSUE_102_production_infra_tls.md) (production
infrastructure) if it lands at all; no issue before M14 assumes a runner exists.

---

## Why a service?

| Method | Survives logout? | Survives reboot? |
|--------|------------------|------------------|
| `./run.sh` (foreground) | No | No |
| `./svc.sh install` + `start` | Yes | Yes (`WantedBy=multi-user.target`) |

`svc.sh install` creates a systemd unit under `/etc/systemd/system/` named:

```text
actions.runner.<owner>-<repo>.<runner-name>.service
```

Example: `actions.runner.Billykat7-clinicQ.clinicq-runner.service`.

---

## Prerequisites

- Linux host with systemd (Ubuntu 22.04+ / Debian 12+)
- A **non-root** user that owns the runner, in the `docker` group because the deploy drives
  `docker compose`. On a host set up by the runbook that is `deploy`, the owner of `/opt/btk/clinicq`;
  `scripts/cd/server-initial-setup.sh` calls its own user `deployer`. Use whichever the host has —
  the runner must not be root.
- A fresh registration token from GitHub → `Billykat7/clinicQ` → **Settings → Actions → Runners →
  New self-hosted runner**. Tokens expire in about an hour, so fetch it immediately before step 1.

`scripts/cd/server-initial-setup.sh` already performs steps 1 and 2 when `GITHUB_RUNNER_TOKEN` is
set (it downloads the pinned runner, checks its SHA-256, configures it unattended, then runs
`svc.sh install` and `svc.sh start`). Use the manual steps below to install a runner on a host that
was provisioned another way, to fix a runner that only runs in the foreground, or to re-enable
auto-start after a reboot.

On a host that already has a runner, use a **different directory**, **`--name`** and **labels** so
the services do not collide. See [clinicq.md](./clinicq.md).

---

## 1. Download and configure the runner

```bash
# The version and digest scripts/cd/server-initial-setup.sh pins today. If GitHub's
# "New self-hosted runner" page offers a newer release, take its version and its SHA-256.
RUNNER_DIR="$HOME/actions-runner"
RUNNER_VERSION="2.332.0"
RUNNER_SHA256="f2094522a6b9afeab07ffb586d1eb3f190b6457074282796c497ce7dce9e0f2a"

mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"

curl -o "actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" -L \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"

echo "${RUNNER_SHA256}  actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" | shasum -a 256 -c
tar xzf "./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
```

Configure:

```bash
cd "$RUNNER_DIR"
./config.sh \
  --url https://github.com/Billykat7/clinicQ \
  --token '<REGISTRATION_TOKEN>' \
  --name clinicq-runner \
  --labels self-hosted,linux,x64,clinicq-deploy \
  --work _work \
  --unattended \
  --replace
```

| Target | `--name` | Label | Workflow `runs-on` |
|--------|----------|-------|--------------------|
| ClinicQ deploy host (this repo) | `clinicq-runner` | `clinicq-deploy` | `[self-hosted, clinicq-deploy]` |
| A separate staging host | `clinicq-staging-runner` | `clinicq-staging` | `[self-hosted, clinicq-staging]` |
| Shared BTK platform (CD) | (infra host) | `infra` | `[self-hosted, Linux, X64, infra]` |

Labels are how a workflow picks the machine, so **give the runner a narrow, repository-specific
label and never target `self-hosted` alone**: a bare `self-hosted` job will happily land on another
product's runner sharing the host.

---

## 2. Install as a systemd service (auto-start on boot)

From the runner directory, as a user with `sudo`:

```bash
cd "$RUNNER_DIR"

# Install the unit and enable it for multi-user.target (starts after reboot)
sudo ./svc.sh install "$(whoami)"

# Start now
sudo ./svc.sh start

# Confirm the listener is up
sudo ./svc.sh status
```

`svc.sh install` both **installs** and **enables** the unit, so after a reboot systemd starts the
runner without an SSH login.

Optional explicit check with systemctl:

```bash
SERVICE="actions.runner.Billykat7-clinicQ.clinicq-runner.service"

sudo systemctl enable "$SERVICE"       # usually already done by svc.sh install
sudo systemctl is-enabled "$SERVICE"   # expect: enabled
sudo systemctl is-active "$SERVICE"    # expect: active
```

---

## 3. Verify

```bash
sudo ./svc.sh status
systemctl list-units --type=service --all | grep actions.runner
systemctl list-unit-files | grep actions.runner
```

In GitHub → **Settings → Actions → Runners**, the runner should show **Idle** (green).

Optional reboot check:

```bash
sudo reboot
# after reconnect:
sudo "$RUNNER_DIR/svc.sh" status
```

---

## 4. Point the deploy at it

`deploy.yml` runs on `ubuntu-24.04` and SSHes to the host. Moving it onto the runner means the job
executes **on** the deploy host, so the SSH hop and its four secrets go away:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, clinicq-deploy]
```

Two things must change with it, or the deploy breaks in a way the runbook does not describe:

- `scripts/cd/deploy.sh` is invoked locally rather than over SSH, and `DEPLOY_DIR`
  (`/opt/btk/clinicq`, or `/opt/btk/clinicq-staging`) becomes a path on the runner's own filesystem.
  Staging and production may share the host — they are separate compose projects on different
  `APP_PORT`s — so one runner can serve both, or each environment can have its own label.
- The GitHub Environment's `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`
  are no longer read. Delete them rather than leaving a live deploy key with nothing to check it.

`tests/unit/platform/test_workflow_guardrails.py` reads the workflow files, so run
`./scripts/ci-local.sh` after the change — a guard test, not review attention, is what notices when
the pipeline's shape moves.

---

## 5. Day-2 operations

```bash
cd "$RUNNER_DIR"

sudo ./svc.sh status
sudo ./svc.sh stop
sudo ./svc.sh start

# After upgrading the runner binary (follow GitHub's upgrade notes), reinstall the service:
sudo ./svc.sh stop
sudo ./svc.sh uninstall
# … upgrade files …
sudo ./svc.sh install "$(whoami)"
sudo ./svc.sh start
```

Logs:

```bash
journalctl -u actions.runner.Billykat7-clinicQ.clinicq-runner.service -f
```

---

## 6. Remove the runner

```bash
cd "$RUNNER_DIR"
sudo ./svc.sh stop
sudo ./svc.sh uninstall
./config.sh remove --token '<REMOVAL_TOKEN>'   # fresh token from the same Runners page
cd ~ && rm -rf "$RUNNER_DIR"
```

---

## Safety notes

- A self-hosted runner executes whatever a workflow tells it to. **This repository is public, so
  never let it run pull requests from forks** — anyone could open one. Keep CI GitHub-hosted, put
  only the deploy on the runner, and leave *Require approval for all external contributors* on under
  Settings → Actions → General.
- Give the runner's user only what a deploy needs: pull an image from GHCR, run `docker compose` in
  `DEPLOY_DIR`, read its `.env`. Never a database superuser, never a cloud administrator key.
- Keep `_work/` on a disk that can be wiped without touching application data; it holds checkouts,
  not state.
- Rotate the registration token if the host is ever shared or handed over, and remove the runner
  from the repository before decommissioning the machine — an Offline runner left registered is a
  name someone can re-register against.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Runner Offline after reboot | `systemctl is-enabled` on the unit; `sudo ./svc.sh start`; `journalctl -u …` |
| Job stuck "Waiting for a runner" | The runner's labels against `runs-on`; runner Idle in the GitHub UI |
| Permission denied for Docker | The user passed to `svc.sh install` is in group `docker`; re-login or reboot after `usermod -aG docker …` |
| Two runners fighting over jobs | Distinct `--name`, directory and labels; `systemctl list-units --type=service \| grep actions.runner` |
| Deploy cannot find `DEPLOY_DIR` | On a self-hosted runner the path is local, not the SSH target's; see step 4 |

Foreground debug (temporary only):

```bash
cd "$RUNNER_DIR"
sudo ./svc.sh stop
./run.sh
# Ctrl+C when done, then: sudo ./svc.sh start
```

---

## Related

- [clinicq.md](./clinicq.md) — a second runner on a host that already has one
- [`docs/CICD/PIPELINES.md`](../../CICD/PIPELINES.md) — every CI job, the one required check, and the measured minutes
- [`docs/CICD/RUNBOOK_DEPLOY.md`](../../CICD/RUNBOOK_DEPLOY.md) — deploying and rolling back
- [`docs/CICD/ENVIRONMENTS.md`](../../CICD/ENVIRONMENTS.md) — what each environment holds
- `scripts/cd/server-initial-setup.sh` — provisioning that installs the runner as a service for you
