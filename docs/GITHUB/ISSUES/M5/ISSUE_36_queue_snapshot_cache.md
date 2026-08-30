# Issue 36: `site_queue_snapshot` Redis cache with warm and invalidate paths

**Area:** Backend / Performance
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Backend Lead
**Depends on:** Issues 25, 31
**Estimate:** 2 days
**Status:** Planned

## Context

Rendering a list of twenty clinics must not run twenty ticket counts. A Redis-backed snapshot, written
when the queue changes and read by discovery, keeps list rendering flat as the directory grows, and it
is what makes the sub-200 ms target in Issue 31 achievable.

## Scope

- `site_queue_snapshot`: site, queue, current length, average wait minutes, updated timestamp
- Write-through invalidation on join, call-next, cancel and no-show
- Redis cache with a short TTL and a database fallback, so a cold cache is slow rather than wrong
- A periodic reconciliation job repairing drift between the snapshot and the true count
- Cache-hit metrics exposed for the M14 performance work

## Acceptance criteria

- [ ] A discovery list of 20 clinics issues one snapshot read, not 20 counts
- [ ] Joining a queue updates the snapshot within 1 second
- [ ] A cold or flushed cache produces correct (slower) results, never stale ones
- [ ] The reconciliation job corrects deliberately corrupted snapshots in a test
- [ ] Cache hit rate is observable
- [ ] Snapshot staleness is bounded and displayed in seconds where the patient can see it

## Files touched

- `app/database/models/site_queue_snapshot.py`
- `app/services/queue_snapshot.py`
- `workers/reconcile_snapshots.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #36
