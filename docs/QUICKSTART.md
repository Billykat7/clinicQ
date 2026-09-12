# Quickstart

From a fresh clone to a running ClinicQ, on **Windows, macOS or Linux** — then how to make a change
and get it merged. Everything here is checked against the repository it describes; when a command
changes, this page changes with it.

| | |
|---|---|
| **1. [Install the prerequisites](#1-install-the-prerequisites)** | Python 3.14, Docker, git, make, `gh` |
| **2. [Run it locally](#2-run-it-locally)** | five commands, then sign in and seed demo data |
| **3. [Make a change](#3-make-a-change-branch-commit-pull-request)** | branch, commit, `make check`, pull request |
| **[Troubleshooting](#troubleshooting)** | ports in use, the local Docker stack |

Once it is running: [CONTRIBUTING.md](../CONTRIBUTING.md) for the day-to-day loop,
[the engineering non-negotiables](guideline.md) for the five rules guard tests enforce, and
[the milestones](GITHUB/README.md) for what is being built next.

---

## 1. Install the prerequisites

| | What | Why | Check it |
|---|---|---|---|
| 🐍 | **Python 3.14** | the app, the tests, Alembic | `python3.14 --version` |
| 🐳 | **Docker Desktop** (or Docker Engine) with **Compose v2.24+** | PostgreSQL 18 + PostGIS and Redis, without installing either | `docker compose version` |
| 🌿 | **Git** | | `git --version` |
| 🔨 | **make** | every command below is a `make` target | `make --version` |
| 🐙 | **GitHub CLI** (`gh`) | raising pull requests, syncing labels and rulesets | `gh --version` |

<details>
<summary><strong>Windows</strong> — use WSL2, and run everything inside it</summary>

Docker Desktop, `make` and the shell scripts in `scripts/` all assume a Unix shell. WSL2 is the
supported path; Git Bash alone will not run the `make` targets.

```powershell
wsl --install -d Ubuntu          # then reboot, and open the Ubuntu terminal
```

Install **Docker Desktop for Windows** and turn on *Settings → Resources → WSL integration* for your
Ubuntu distribution. Everything from here runs **inside the Ubuntu shell**, not PowerShell:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update
sudo apt install -y python3.14 python3.14-venv git make gh
```

Clone into the Linux filesystem (`~/code/...`), **not** `/mnt/c/...`: builds and file watching are
several times slower across the Windows mount.

</details>

<details>
<summary><strong>macOS</strong> — Homebrew</summary>

```bash
brew install python@3.14 git make gh
brew install --cask docker        # then launch Docker Desktop once, to finish setup
```

`make` ships with the Xcode command line tools (`xcode-select --install`) if you would rather not
use Homebrew's. **Apple Silicon:** `postgis/postgis` publishes amd64 images only, so the database
container runs under emulation — it works, and the first start is slower.

</details>

<details>
<summary><strong>Linux</strong> — Debian/Ubuntu, Fedora, Arch</summary>

```bash
# Debian / Ubuntu
sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update
sudo apt install -y python3.14 python3.14-venv git make gh docker.io docker-compose-plugin

# Fedora
sudo dnf install -y python3.14 git make gh docker docker-compose-plugin

# Arch
sudo pacman -S python git make github-cli docker docker-compose
```

Then add yourself to the `docker` group so the compose targets do not need `sudo`:

```bash
sudo usermod -aG docker "$USER" && newgrp docker
```

</details>

## 2. Run it locally

Five commands, from a fresh clone:

1. Create the virtual environment and install the dependencies:

   ```bash
   python3.14 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   ```

2. Point the app at the local stack. `.env.example` lists every setting the app reads, each with
   its description and default; only `DATABASE_URL` and `REDIS_URL` are active, matching the compose
   defaults. What differs in staging and production, and what the app refuses to start with there,
   is in [`docs/CICD/ENVIRONMENTS.md`](CICD/ENVIRONMENTS.md):

   ```bash
   cp .env.example .env
   ```

3. Start PostgreSQL 18 with PostGIS, and Redis. The command returns once both health checks pass:

   ```bash
   make db-up
   ```

4. Create the schema (the `clinicq` schema, with PostGIS enabled):

   ```bash
   make migrate-up
   ```

5. Run the app with auto-reload:

   ```bash
   make run
   ```

Then open `http://127.0.0.1:8000` for the landing page, `http://127.0.0.1:8000/docs` for the API, and
`http://127.0.0.1:8000/health/ready`, which should report the database and migrations as `ok`.
After the first time, `make dev` does steps 3 and 5 in one go.

**To sign in**, seed the roles and a development admin, then sign in as `admin@btk.com` with the
password you chose:

```bash
make seed-rbac && ./scripts/db/seed-dev-user.sh --password 'choose-a-password'
```

**For demo data**, `make seed-dev-data` adds one staff account per ClinicQ role (printed with their
development password) and reports the demo clinics, queues and ticket history in
`scripts/db/demo_dataset.py`: eleven real Gauteng and KwaZulu-Natal clinics, written to the database
as their tables land (Issues 23, 25 and 39). It is idempotent, and refuses any database that is not
a local development one.

## 3. Make a change: branch, commit, pull request

One issue, one branch, one pull request. The names are checked by CI, so they are not a matter of
taste — the **Conventions** job fails a branch, a commit or a description that does not follow this.

```bash
# 1. Start from an up-to-date main
git checkout main && git pull

# 2. Branch: Issue/<number>/<short-slug>, five words at most
git checkout -b Issue/39/tickets-model-sequence

# 3. Work, then commit. Every commit subject starts "Issue <number>: "
git commit -m "Issue 39: Add the concurrency-safe ticket sequence"

# 4. Run the same gate CI runs, before you push
make check

# 5. Write the description, then open the pull request with it
#    docs/GITHUB/PR/M6/PR_39_DESCRIPTION.md — ending in "Closes #39"
git push -u origin Issue/39/tickets-model-sequence
gh pr create --base main \
  --title "Issue 39: Tickets model and daily sequence" \
  --body-file docs/GITHUB/PR/M6/PR_39_DESCRIPTION.md \
  --milestone "Milestone 6: Queue Engine Core" --assignee @me
```

What the checks expect, and why:

| Rule | Example | Checked by |
|---|---|---|
| Branch `Issue/<N>/<slug>` (or `Release/v<X.Y.Z>`) | `Issue/39/tickets-model-sequence` | `scripts/check_pr_conventions.py` |
| Every commit subject starts `Issue <N>: ` | `Issue 39: Add the ticket sequence` | same — merge commits are skipped |
| The description ends with `Closes #<N>` | `Closes #39` | same |
| A screenshot when `src/templates/` or `src/static/` changed | an image in the body | same |
| **The milestone's progress is updated in the same PR** | `make milestone-progress` | [CONTRIBUTING.md](../CONTRIBUTING.md#branches-commits-and-pull-requests) |
| The CI gate is green, and a code owner approves | | branch ruleset on `main` |

Write the description **before** opening the pull request, in
`docs/GITHUB/PR/M<milestone>/PR_<issue>_DESCRIPTION.md`, and prove each acceptance criterion by
demonstration — real command output, a before/after failure — rather than by describing the code.
[`PR_1_DESCRIPTION.md`](GITHUB/PR/M1/PR_1_DESCRIPTION.md) is the model.

## Troubleshooting

If something is already using a port: `DB_PORT=5433 make db-up` starts the database on another port
(put the same port in `DATABASE_URL`), `REDIS_PORT` does the same for Redis (and `REDIS_URL`), and
`make run PORT=8001` moves the app. To keep a port for every `make` target, put `DB_PORT=5433` (and
`REDIS_PORT`, `HTTP_PORT`) in `infra/docker/.env` instead: it is git-ignored and Compose reads it on
every command. Set `DATABASE_URL` itself rather than the separate `DB_HOST` / `DB_USER` /
`DB_PASSWORD` / `DB_NAME` settings: those are only combined into a URL when `DATABASE_URL` is not a
plain `postgresql://` URL, so on their own they are ignored. If you pass `--email` to the seed
script, use a real-looking domain: the sign-in API rejects reserved ones such as `.local`.

## The local Docker stack

`infra/docker/docker-compose.yml` is one compose project, `clinicq`. It includes the database and
Redis from `infra/docker/docker-compose.db.yml` and adds the API container behind nginx:

| Service | Image | On the host | Data |
|---------|-------|-------------|------|
| `db` | `postgis/postgis:18-3.6` (PostgreSQL 18, PostGIS 3.6) | `127.0.0.1:5432` (`DB_PORT`) | volume `clinicq_pgdata` |
| `redis` | `redis:8-alpine` | `127.0.0.1:6379` (`REDIS_PORT`) | volume `clinicq_redisdata` |
| `app` + `nginx` | built from `infra/docker/Dockerfile` | `http://localhost:8000` (`HTTP_PORT`) | |

- `make db-up` / `make db-down` start and stop `db` and `redis` (from `infra/docker`, plain
  `docker compose up -d db redis` does the same). Stopping keeps the data; it is in the volumes.
- `make docker-up` runs everything in containers, API included. The container is given the same
  `DATABASE_URL` and `REDIS_URL` as `.env.example` with the host changed to `db` and `redis`, and
  `make migrate-up` from the host migrates the same database.
- **Wipe and recreate from scratch** (deletes all local data in both volumes):

  ```bash
  make db-reset
  ```

  It runs `down -v --remove-orphans` on the compose project, then `make db-up`. Follow it with
  `make migrate-up`.
- **Coming from PostgreSQL 16:** the stack used to run `postgis/postgis:16-3.4` as a project named
  `clinicq-db`, whose data PostgreSQL 18 cannot open. The new stack starts in a new, empty volume and
  leaves the old one alone. Local data is rebuilt by `make migrate-up` and the seeds, so once you
  have nothing to keep, remove the old container and volume with `docker compose -p clinicq-db down -v`.
  Until you do, the old container restarts with Docker and holds port 5432, so `make db-up` reports
  the port as taken.
- **Apple Silicon:** `postgis/postgis` publishes amd64 images only, so the compose file asks for
  `linux/amd64` and Docker Desktop runs it under emulation. It works; the first start is slower.

**Before every push**, run the same gate CI runs:

```bash
make check
```

`make check-compose` adds a smoke test of this stack (PostGIS and Redis answer, `/health/live` is
up), in a separate compose project on other ports so it never touches your local data.

The pull request then runs the same gate in GitHub Actions against real PostgreSQL + PostGIS and
Redis, and cannot merge until its **CI gate** check is green
([`docs/CICD/PIPELINES.md`](CICD/PIPELINES.md)). A few kernel guard tests still wait for files a
later issue creates (the deploy and scan workflows, `docs/SECURITY/`); they run as strict expected
failures (`PENDING_ON_LATER_ISSUES` in `tests/conftest.py`), so `make test` is green meanwhile.
