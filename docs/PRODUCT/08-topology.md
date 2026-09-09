# 08 - System & network topology

Start with one clinic on its existing internet connection, then grow to many clinics reporting into one
shared platform, the same site-by-site growth model as
ElimuKadi's topology, but even lighter since there's no edge reader firmware to
maintain, just a browser-based signage box and dashboard.

## Phase 0 - one clinic, one display, one dashboard

```mermaid
flowchart LR
  DASH[Reception/nurse dashboard - PC/tablet] -->|Wi-Fi/LAN| NET[Clinic internet - Wi-Fi/LAN or LTE]
  DISP[Display monitor - Pi kiosk box] -->|Wi-Fi/LAN| NET
  NET -->|HTTPS| API[ClinicQ API - cloud VPS]
  API --> DB[(PostgreSQL 18+ / PostGIS)]
  PATIENT((Patient phone - PWA/USSD/WhatsApp)) -->|Internet| API
```

Unlike ElimuKadi or
UmojaNet, ClinicQ's API is **cloud-hosted from
day one** (even for a single pilot clinic) since there's no local edge hardware that needs an on-site
gateway to function: a dashboard and a display box are just browser clients of a normal web API.

## Phase 1 - a few clinics, shared backend

```mermaid
flowchart TB
  C1[Clinic A: dashboard + display]
  C2[Clinic B: dashboard + display]
  C3[Clinic C: dashboard + display]
  API[ClinicQ API - cloud VPS, multi-tenant]
  DB[(PostgreSQL 18+ / PostGIS)]
  USSD_GW[USSD gateway - Africa's Talking-class]
  WA_GW[WhatsApp Business API]
  C1 -->|HTTPS| API
  C2 -->|HTTPS| API
  C3 -->|HTTPS| API
  USSD_GW -->|Webhook| API
  WA_GW -->|Webhook| API
  API --> DB
  API --> NOTIF[SMS/push/WhatsApp notifications]
```

## Phase 2 - metro-wide discovery + many clinics

```mermaid
flowchart TB
  API[ClinicQ API cluster]
  DB[(PostgreSQL 18+ w/ PostGIS, primary + replica)]
  REDIS[(Redis - cache/queues)]
  CLINICS[20-100+ clinics: dashboard + display each]
  PUBLIC[Public discovery site/app - city-wide clinic map]
  ADMIN[Platform admin dashboard]
  CLINICS -->|HTTPS| API
  PUBLIC -->|HTTPS| API
  API --> DB
  API --> REDIS
  API --> ADMIN
```

At this scale, PostGIS geo-queries and the queue-length cache ([13](13-tech-implementation.md)) both live
behind Redis to keep the "clinics near me" search fast even as the clinic count grows into the hundreds.

## Resilience: internet outage at a clinic

Because the API is cloud-hosted (unlike the offline-first, load-shedding-tolerant edge design needed for
ElimuKadi's card readers),
ClinicQ's main resilience concern is simpler: **what happens to a clinic's queue if its internet drops
for a few minutes.**

```mermaid
sequenceDiagram
    participant D as Dashboard/Display (clinic)
    participant API as ClinicQ API
    D->>D: Local cache of "current queue state" (last known good, in browser storage)
    D->>API: Poll/heartbeat
    alt Reachable
        API-->>D: Fresh state, cache updated
    else Offline (brief outage)
        D->>D: Show cached state + "reconnecting..." banner, keep last-known board visible
        Note over D: Reception can still call names verbally during a short outage - the physical room doesn't stop
    end
```

A short outage degrades the **notification** and **remote discovery** experience (patients can't newly
join or get notified) but does not stop the clinic from running its physical queue verbally in the
meantime, a deliberately low-stakes failure mode compared to a payment-carrying system.

## Pros & cons

| | |
|---|---|
| **Pros** | Near-zero clinic hardware; cloud-hosted from day one (simple ops); one queue engine behind four patient-facing channels; PostGIS gives a real "clinics near me" experience cheaply |
| **Cons** | Fully dependent on internet reachability for remote-join/notifications (mitigated by verbal fallback in the room); USSD/WhatsApp gateway costs scale with message volume; discovery data (clinic hours, sector, payment tags) needs upkeep to stay trustworthy |

Markdown: this is [08-topology.md](08-topology.md); see wiring/hosting guidance in
[13-tech-implementation.md](13-tech-implementation.md#9-hosting-choice).
