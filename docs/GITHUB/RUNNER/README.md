# Self-hosted runner

CI runs on GitHub-hosted runners and draws on the Free plan's 2,000 minutes a month. **Deployment and
smoke checks run on a self-hosted runner instead, because self-hosted minutes are not billed**, which
is what makes deploying a release effectively free.

## When the team needs one

Not on day one. Add a self-hosted runner when either is true:

- Deploys become frequent enough that build minutes are a real constraint (roughly: more than one
  release a day for a sustained period), or
- The deploy target is a machine that is not reachable from a GitHub-hosted runner (a VPS behind a
  firewall, or a machine on a home connection).

## Setup sketch

1. Provision the runner on the same host as the staging or production stack, as a non-root user.
2. Register it against the repository with a narrowly scoped label (`clinicq-deploy`), not `self-hosted`
   alone: labels are how a workflow picks the right machine.
3. Install it as a **systemd service** so it restarts after a reboot:
   `sudo ./svc.sh install <user> && sudo ./svc.sh start`.
4. Target it from the deploy workflow: `runs-on: [self-hosted, clinicq-deploy]`.
5. Give the runner only the credentials it needs to pull an image and restart compose, never a
   database superuser or a cloud administrator key.

## Safety notes

- A self-hosted runner executes whatever a workflow tells it to. **Never enable it for pull requests
  from forks.** This repository's CI is GitHub-hosted for exactly that reason; only the tag-triggered
  deploy touches the self-hosted machine.
- Keep the runner's work directory on a disk that can be wiped without touching application data.
- Rotate the registration token if the host is ever shared or handed over.
