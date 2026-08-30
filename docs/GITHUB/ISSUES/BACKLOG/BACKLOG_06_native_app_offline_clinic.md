# Backlog 06: Native app and offline-first clinic mode

**Area:** Mobile / Infrastructure · **Post-capstone** · **Depends on:** M9, M14

## Context

The PWA covers the patient side well. Two things it cannot do sit in the backlog: reliable background
notifications on iOS, and a clinic that keeps running its queue through a multi-hour internet outage.
The second matters more: load-shedding and rural connectivity are facts of the operating environment,
and topology doc 08 already flags resilience as a design concern.

## Rough scope when picked up

- Offline-capable local queue at the clinic, syncing to the cloud when connectivity returns
- Conflict resolution rules for tickets issued on both sides of a partition
- A thin native shell (patient) for reliable push where the PWA cannot deliver
- A local-only degraded mode for the dashboard and board, clearly signposted to staff
