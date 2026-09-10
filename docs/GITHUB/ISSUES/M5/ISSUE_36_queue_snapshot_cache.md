# Issue 36: `site_queue_snapshot` Redis cache with warm and invalidate paths

> **In short:** Discovery shows live queue lengths for twenty clinics with one fast read, never a stale number, even when the cache is cold.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 5–6 (weeks 9–12), from the milestone window (not yet placed in a lane) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Performance |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site<br>[Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA |
| **Unblocks** | [Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract |

> **Note:** This issue is not placed in any lane of the sprint plan; the milestone window (sprints 5–6) is assumed. Place it before sprint 5 planning.

## Context

Rendering a list of twenty clinics must not run twenty ticket counts. A Redis-backed snapshot, written
when the queue changes and read by discovery, keeps list rendering flat as the directory grows, and it
is what makes the sub-200 ms target in Issue 31 achievable.

## Starting point

- Redis is available (the kernel's rate-limit backend already talks to it through `src/core/rate_limit_backend.py`).
- The reconciliation job is a sweep registered in `src/core/scheduler.py` (APScheduler with an advisory lock), not an `arq` worker; see [open decisions](../README.md#open-decisions).

## Scope

- `site_queue_snapshot`: site, queue, current length, average wait minutes, updated timestamp
- Write-through invalidation on join, call-next, cancel and no-show
- Redis cache with a short TTL and a database fallback, so a cold cache is slow rather than wrong
- A periodic reconciliation job repairing drift between the snapshot and the true count
- Cache-hit metrics exposed for the M14 performance work

## Out of scope

- Performance tuning and dashboards (Issue 105).
- The board's live state, which is pushed by SSE (Issue 57).

## Acceptance criteria

- [ ] A discovery list of 20 clinics issues one snapshot read, not 20 counts
- [ ] Joining a queue updates the snapshot within 1 second
- [ ] A cold or flushed cache produces correct (slower) results, never stale ones
- [ ] The reconciliation job corrects deliberately corrupted snapshots in a test
- [ ] Cache hit rate is observable
- [ ] Snapshot staleness is bounded and displayed in seconds where the patient can see it

## How to verify

1. Load a list of 20 clinics with query logging on: one snapshot read.
2. Join a queue: the snapshot changes within 1 second.
3. Flush Redis, then search: results are correct, just slower.
4. Corrupt a snapshot row by hand: the next reconciliation run repairs it.

## Files touched

- `src/modules/queue/snapshot.py`
- `src/database/models/site_queue_snapshot.py`
- `src/core/scheduler.py`
- `tests/integration/queue/test_queue_snapshot.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #36
