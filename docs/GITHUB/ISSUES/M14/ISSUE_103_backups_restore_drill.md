# Issue 103: Backups, restore drill and disaster-recovery runbook

**Area:** Infra / Production
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 102
**Estimate:** 2 days
**Status:** Planned

## Context

A backup that has never been restored is a hypothesis. This issue turns it into a measured fact, with
a written recovery objective and a drill the team has actually performed and timed.

## Scope

- Automated daily database backups with encryption and off-site storage
- Documented retention: daily for 30 days, weekly for 3 months
- A performed and timed restore drill into a clean environment
- Stated RPO and RTO with evidence that both are met
- A disaster-recovery runbook covering database loss, host loss and a bad deploy

## Acceptance criteria

- [ ] Backups run daily and are verified as restorable, not merely present
- [ ] A full restore has been performed end to end and the elapsed time recorded
- [ ] RPO and RTO are stated and evidenced by the drill
- [ ] Backups are encrypted and stored off the production host
- [ ] The runbook is specific enough to follow under pressure
- [ ] A restore-failure alert exists and has been tested

## Files touched

- `infra/backup/`
- `docs/OPS/DR_RUNBOOK.md`
- `scripts/restore_drill.sh`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #103
