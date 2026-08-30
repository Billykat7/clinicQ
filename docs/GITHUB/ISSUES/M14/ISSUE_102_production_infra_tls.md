# Issue 102: Production infrastructure, TLS, domains and edge protection

**Area:** Infra / Production
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 11, 98
**Estimate:** 3 days
**Status:** Planned

## Context

Moving from a laptop and a staging box to something a clinic depends on at 07:00. The requirements
change accordingly: certificates renew themselves, the database is managed rather than hand-nursed, and
nothing is reachable that does not need to be.

## Scope

- Production VPS provisioning with a documented, repeatable setup
- Managed PostgreSQL with PostGIS, and managed Redis, in the closest region
- Domain, DNS and automated TLS certificate renewal
- Reverse proxy with rate limiting, request-size limits and sensible timeouts
- Firewall rules exposing only what is required, with database access closed to the public internet

## Acceptance criteria

- [ ] The production stack serves HTTPS on the project domain with an A rating on an SSL test
- [ ] Certificates renew automatically, verified by forcing a renewal
- [ ] The database is unreachable from the public internet
- [ ] Provisioning is documented well enough to be repeated from scratch
- [ ] Resource sizing is justified against the load test in Issue 105
- [ ] The production environment refuses to boot with any development setting

## Files touched

- `infra/production/`
- `docs/OPS/PROVISIONING.md`
- `infra/docker-compose.prod.yml`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #102
