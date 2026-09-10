# Issue 102: Production infrastructure, TLS, domains and edge protection

> **In short:** A production environment the team can rebuild from its own notes: HTTPS on the real domain, a database nobody on the internet can reach, and nothing left at development settings.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Production |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 11](../M2/ISSUE_11_cd_staging_prod_approval.md): CD: staging deploy on tag, production behind manual approval<br>[Issue 98](../M13/ISSUE_98_encryption_pii_protection.md): Encryption in transit and at rest, field-level encryption for patient contacts |
| **Unblocks** | [Issue 103](../M14/ISSUE_103_backups_restore_drill.md): Backups, restore drill and disaster-recovery runbook<br>[Issue 104](../M14/ISSUE_104_monitoring_alerting_status.md): Monitoring, alerting, on-call rota and status page<br>[Issue 105](../M14/ISSUE_105_load_soak_testing.md): Load and soak testing of the morning-rush profile<br>[Issue 106](../M14/ISSUE_106_pilot_rollout_kit.md): Pilot rollout kit: site survey, install guide, staff training pack |

## Context

Moving from a laptop and a staging box to something a clinic depends on at 07:00. The requirements
change accordingly: certificates renew themselves, the database is managed rather than hand-nursed, and
nothing is reachable that does not need to be.

## Starting point

- `scripts/cd/` already provisions a Hetzner server (`create-hetzner-server.sh`, cloud-init) and writes the production env file; `infra/nginx/` holds reverse-proxy configs and `infra/docker/docker-compose.prod.yml` runs the app, a one-shot `migrate` service and a Redis rate-limit store.
- The kernel expects to share a platform database (`btk`) with its tables in the `clinicq` schema (`DbSchema` in `src/commons/enums.py`). Decide early whether production follows that model or gets a dedicated database.

## Scope

- Production VPS provisioning with a documented, repeatable setup
- Managed PostgreSQL 18 with PostGIS 3.6 (decision 4, the versions the dev stack and CI run), and managed Redis, in the closest region
- Domain, DNS and automated TLS certificate renewal
- Reverse proxy with rate limiting, request-size limits and sensible timeouts
- Firewall rules exposing only what is required, with database access closed to the public internet

## Out of scope

- Backups (Issue 103) and monitoring (Issue 104).

## Acceptance criteria

- [ ] The production stack serves HTTPS on the project domain with an A rating on an SSL test
- [ ] Certificates renew automatically, verified by forcing a renewal
- [ ] The database is unreachable from the public internet
- [ ] Provisioning is documented well enough to be repeated from scratch
- [ ] Resource sizing is justified against the load test in Issue 105
- [ ] The production environment refuses to boot with any development setting

## How to verify

1. Run an SSL test against the domain: grade A.
2. Force a certificate renewal: it completes without anyone logging in.
3. Try to connect to the database from outside the host: refused.
4. Someone other than the author rebuilds a server from `PROVISIONING.md`.

## Files touched

- `scripts/cd/`
- `infra/nginx/`
- `infra/docker/docker-compose.prod.yml`
- `docs/OPS/PROVISIONING.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #102
