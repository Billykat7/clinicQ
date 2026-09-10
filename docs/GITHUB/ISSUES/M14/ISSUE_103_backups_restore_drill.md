# Issue 103: Backups, restore drill and disaster-recovery runbook

> **In short:** If the database or the server is lost, the team can bring ClinicQ back within a known time, and has already proved it.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Production |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 102](../M14/ISSUE_102_production_infra_tls.md): Production infrastructure, TLS, domains and edge protection |
| **Unblocks** | No other issue waits on this one. |

## Context

A backup that has never been restored is a hypothesis. This issue turns it into a measured fact, with
a written recovery objective and a drill the team has actually performed and timed.

## Starting point

- `scripts/db/backup.sh` already takes an encrypted schema dump, ships it off the host, prunes old copies and pings a dead-man's switch; `scripts/db/restore.sh` restores it.
- What is left: scheduling it in production, the retention periods in the criteria, a timed drill and the runbook.

## Scope

- Automated daily database backups with encryption and off-site storage
- Documented retention: daily for 30 days, weekly for 3 months
- A performed and timed restore drill into a clean environment
- Stated RPO and RTO with evidence that both are met
- A disaster-recovery runbook covering database loss, host loss and a bad deploy

## Out of scope

- Application monitoring (Issue 104).

## Acceptance criteria

- [ ] Backups run daily and are verified as restorable, not merely present
- [ ] A full restore has been performed end to end and the elapsed time recorded
- [ ] RPO and RTO are stated and evidenced by the drill
- [ ] Backups are encrypted and stored off the production host
- [ ] The runbook is specific enough to follow under pressure
- [ ] A restore-failure alert exists and has been tested

## How to verify

1. Restore last night's backup into a clean environment: the app starts and the data is there; record the time.
2. Stop the backup job: the dead-man's switch alerts.
3. Follow the runbook for "host lost" start to finish in a rehearsal.

## Files touched

- `scripts/db/backup.sh`
- `scripts/db/restore.sh`
- `scripts/restore_drill.sh`
- `docs/OPS/DR_RUNBOOK.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #103
