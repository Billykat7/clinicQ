# Self-hosted runner for `Billykat7/clinicQ`

For the case where the target server **already runs another self-hosted runner** — the shared BTK
platform runner, or another product's. Do not touch that one. Install ClinicQ's into its own
directory (`~/actions-clinicq`) with its own name, labels and systemd service, so the two never
collide over a job or a work folder.

For the full **service + auto-start after reboot** procedure, and for whether ClinicQ needs a runner
at all, see **[README.md](./README.md)**. Short version: nothing in this repository needs one today —
`deploy.yml` runs on `ubuntu-24.04` and reaches the host over SSH.

## 0. Check what is already there

```bash
# Existing runner directories
ls -d ~/actions-* 2>/dev/null

# Existing runner services (note the names so you pick a different one)
systemctl list-units --type=service | grep actions.runner
```

Each runner needs a unique:

- **directory** — `~/actions-clinicq` here
- **runner name** (`--name`) — must be unique within the repository
- **service name** — derived as `actions.runner.<owner>-<repo>.<name>.service`, so a unique `--name`
  is enough
- **work folder** (`--work`) — lives inside the runner directory, so it is isolated already

## 1. Create the directory and download the runner

```bash
mkdir -p ~/actions-clinicq && cd ~/actions-clinicq

curl -o actions-runner-linux-x64-2.332.0.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.332.0/actions-runner-linux-x64-2.332.0.tar.gz

echo "f2094522a6b9afeab07ffb586d1eb3f190b6457074282796c497ce7dce9e0f2a  actions-runner-linux-x64-2.332.0.tar.gz" | shasum -a 256 -c

tar xzf ./actions-runner-linux-x64-2.332.0.tar.gz
```

That version and digest are the ones `scripts/cd/server-initial-setup.sh` pins. A newer release is
fine — take both the version *and* its SHA-256 from GitHub's page, and never skip the `shasum` check.

## 2. Get a fresh registration token

Tokens expire after about an hour, so generate one immediately before configuring:

GitHub → `Billykat7/clinicQ` → **Settings** → **Actions** → **Runners** → **New self-hosted runner**,
and copy the token out of the `./config.sh` line it shows.

## 3. Configure

```bash
cd ~/actions-clinicq
./config.sh \
  --url https://github.com/Billykat7/clinicQ \
  --token '<REGISTRATION_TOKEN>' \
  --name clinicq-runner \
  --labels self-hosted,linux,x64,clinicq \
  --work _work \
  --unattended \
  --replace
```

- `--name clinicq-runner` keeps it distinct from the other runners on the host.
- `--labels …,clinicq` is how a workflow targets **this** runner:
  `runs-on: [self-hosted, Linux, X64, clinicq]`. `Linux` and `X64` the runner adds itself; add
  `infra` too if this is the shared platform runner the other BTK products deploy through.
- `--replace` only replaces a runner **with the same name**, so it cannot disturb the existing one.

## 4. Install as a service (survives reboot)

Do **not** use `./run.sh` for a permanent install — it stops when the shell exits. `svc.sh install`
registers a systemd unit and **enables it on boot**.

```bash
cd ~/actions-clinicq
sudo ./svc.sh install "$(whoami)"
sudo ./svc.sh start
sudo ./svc.sh status
```

This creates `actions.runner.Billykat7-clinicQ.clinicq-runner.service`, separate from the other
runners' services.

Confirm it is enabled for auto-start:

```bash
systemctl is-enabled actions.runner.Billykat7-clinicQ.clinicq-runner.service
# expect: enabled
```

To run it in the foreground once, for debugging only:

```bash
cd ~/actions-clinicq && ./run.sh
```

## 5. Verify

```bash
sudo ~/actions-clinicq/svc.sh status
systemctl list-units --type=service | grep actions.runner
```

Then confirm the runner shows **Idle** under Settings → Actions → Runners in GitHub, and that the
host's other runner is still Idle too.

## 6. Target this runner from a workflow

```yaml
jobs:
  deploy:
    runs-on: ${{ fromJSON(vars.DEPLOY_RUNNER_LABELS || '["self-hosted","Linux","X64","clinicq"]') }}
```

Without the `clinicq` label, a job using plain `runs-on: self-hosted` may land on the other runner on
the same host — which has neither ClinicQ's deploy directory nor its `.env`.

`deploy.yml` already runs here (Issue 230), so there is no SSH hop and no `DEPLOY_*` secret left to
set; see [README.md § The deploy already points at it](./README.md#4-the-deploy-already-points-at-it-issue-230),
and run `./scripts/ci-local.sh` after any change to the workflow.

## Sharing the host with staging

Staging and production are separate compose projects (`btk-clinicq-staging` and `btk-clinicq`) on
different `APP_PORT`s, so they can share one machine
([RUNBOOK_DEPLOY.md](../../CICD/RUNBOOK_DEPLOY.md#setting-up-a-host-once-per-environment)). One
runner can serve both environments — each GitHub Environment names its own `DEPLOY_DIR` (and any
`APP_ENV` that differs), so the deploy lands in the right directory for the chosen one. Register
a second runner only when the two live on different hosts, and then give it its own label
(`clinicq-staging`) and point staging at it with the `DEPLOY_RUNNER_LABELS` variable.

## Removing this runner

```bash
cd ~/actions-clinicq
sudo ./svc.sh stop
sudo ./svc.sh uninstall
./config.sh remove --token '<REMOVAL_TOKEN>'   # fresh token from the same Runners page
cd ~ && rm -rf ~/actions-clinicq
```

Remove it from the repository before the machine is decommissioned: an Offline runner left
registered is a name someone else can re-register against.
