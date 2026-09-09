# ClinicQ (`clinicq`)

> **Naming.** The capstone shortlist card called this idea **ClinicQueue** (`clinic-queue`). The team
> settled on **ClinicQ** (`clinicq`): shorter, easier to say on a phone call, and it keeps the "Q"
> that is the whole point of the product: a queue you hold from your pocket. Earlier drafts of these
> docs explored regional-language wordmarks (*AfyaNamba*, "health number") for a later multi-country
> rollout; those stay parked as expansion brands and are not used anywhere in the codebase.

**Idea:** find a nearby clinic (public or private, later filterable by accepted medical aid/cash/card
under private), join its digital queue before you even leave home (from a **mobile PWA, USSD menu,
WhatsApp bot, or plain web app**) and watch your ticket number count down on the clinic's own
waiting-room **display monitor**, while staff run the whole line from one **dashboard**.

> Capstone shortlist origin: [docs/PROJECTS](../PROJECTS/index.html#clinic-queue) (was **ClinicQueue**)

## Quick answers

| Question | Short answer |
|----------|--------------|
| Where does the name come from? | **ClinicQ:** clinic + queue. Shortlisted as "ClinicQueue"; shortened for the wordmark |
| What's new vs the original idea? | Same queue-management core, **plus** geo-location clinic discovery with a public/private toggle, and four access channels (app, USSD, WhatsApp, web) instead of one |
| How does geo-location work? | PostGIS radius/distance search against clinic locations, from phone GPS or a typed area name - [02](02-discovery-and-geolocation.md) |
| What does the public/private toggle do? | Filters clinics by sector; payment filters (medical aid/cash/card) only ever apply under "Private", and only from Phase 2 - [02](02-discovery-and-geolocation.md) |
| What shows on the waiting-room screen? | Ticket number + time always; name and comment (e.g. "headache", "follow-up") are **clinic-configurable, off by default** for privacy - [04](04-display-monitor.md) |
| App needed? | **PWA** for patients/clinics is enough; **USSD** covers feature phones/no-data; **WhatsApp** covers the channel most people already use daily - [06](06-channels-app-ussd-whatsapp-web.md) |
| Devices needed? | A monitor/TV + a small signage box (Pi, ~$45-80) and a reused reception PC - see [07](07-devices-and-bom.md) |
| Startup cash for one pilot clinic? | **~R1,800-15,000 (~$100-830)** depending on whether a screen/PC already exists - the cheapest of the elaborated shortlist directions ([09](09-pricing-and-budget.md)) |
| Repo name / structure? | Suggested `clinicq`; docs-only preview (no code, no remote repo) in [REPO_README.md](REPO_README.md) |

## Doc map

| Tab / file | Topic |
|------------|--------|
| [01](01-overview.md) | Name, vision, actors, modules, stack |
| [02](02-discovery-and-geolocation.md) | Clinic discovery: geo-location, public/private toggle, medical aid/cash/card filters (later) |
| [03](03-booking-and-queue.md) | Booking & digital queue: join remotely or walk-in, ticket numbers, wait estimates |
| [04](04-display-monitor.md) | Waiting-room display monitor: number, time, name/initials, optional comment - privacy design |
| [05](05-clinic-dashboard.md) | Clinic dashboard: call-next, per-room queues, walk-in intake, reporting |
| [06](06-channels-app-ussd-whatsapp-web.md) | Mobile PWA, USSD, WhatsApp bot, Web App - one engine, four doors |
| [07](07-devices-and-bom.md) | What to buy (display screens, signage box, reception hardware) - cheapest first |
| [08](08-topology.md) | System & network topology, phases |
| [09](09-pricing-and-budget.md) | Per-clinic pricing, startup budget, break-even |
| [10](10-business-plan.md) | How to launch the business |
| [11](11-marketing.md) | How to market this to clinics |
| [12](12-upscaling-24-months.md) | Upscaling journey, revenue projections to 24 months |
| [13](13-tech-implementation.md) | Tech stack (Python 3.14 - FastAPI - PostGIS - htmx), dashboard, device counts, wiring |
| [14](14-benchmark.md) | Tech used by global queue/appointment systems, extra features, monetisation vs incumbents |
| [REPO_README.md](REPO_README.md) | Suggested repo name (`clinicq`), project structure, env vars, setup - **docs only, no code or remote repo created** |

**Browse:** open [`index.html`](index.html) - tabbed UI (inactive tabs are hidden via `[hidden]`), with
live-rendered **Mermaid** diagrams (discovery/geo sequence, queue-join sequence, display-monitor flow,
dashboard call-next sequence, 90-day Gantt, growth gates, device wiring, positioning quadrant) via
mermaid.js, the same source as the fenced ```mermaid blocks in [02](02-discovery-and-geolocation.md),
[03](03-booking-and-queue.md), [04](04-display-monitor.md), [05](05-clinic-dashboard.md),
[06](06-channels-app-ussd-whatsapp-web.md), [08](08-topology.md), [10](10-business-plan.md),
[11](11-marketing.md), [12](12-upscaling-24-months.md), [13](13-tech-implementation.md), and
[14](14-benchmark.md), which GitHub also renders natively.

## Mental model (one queue, four doors in)

```text
                +---------------------- ClinicQ queue engine ----------------------+
                |                                                                     |
   Mobile PWA -------\                                                                |
   USSD menu ----------> JOIN QUEUE (ticket number, live position, ETA)               |
   WhatsApp bot -------/                                                               |
   Walk-in (reception) /                                                              |
                |                                                                     |
   Clinic dashboard --> CALL NEXT --> updates: patient's phone/USSD/WhatsApp + waiting-room display |
                +---------------------------------------------------------------------+
                    Same queue, same ticket number, no matter which door you came in
```
