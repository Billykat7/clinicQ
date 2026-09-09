# 01 - Overview - ClinicQ

## Name

> **Naming.** The capstone shortlist card called this idea **ClinicQueue** (`clinic-queue`). The team
> settled on **ClinicQ** (`clinicq`): shorter, easier to say on a phone call, and it keeps the "Q"
> that is the whole point of the product: a queue you hold from your pocket. Earlier drafts of these
> docs explored regional-language wordmarks (*AfyaNamba*, "health number") for a later multi-country
> rollout; those stay parked as expansion brands and are not used anywhere in the codebase.

Repo / slug: **`clinicq`**.

## Product in one sentence

**Find a nearby clinic (public or private), join its digital queue from your phone or a USSD/WhatsApp
menu before you even leave home, and watch your position count down on the clinic's waiting-room screen
- while the clinic staff run the whole line from one dashboard.**

## What we are *not* claiming / not building

- **Not a full hospital information system (HIS/EMR).** No diagnosis coding, no pharmacy dispensing, no
  lab-result storage in the MVP. ClinicQ is a **queue and discovery layer** in front of whatever clinic
  system already exists (or none at all, for the many clinics still running paper files); see
  [14-benchmark.md](14-benchmark.md).
- **Not a medical-aid claims/switching platform.** The "accepts medical aid X / cash / card" filter
  ([02](02-discovery-and-geolocation.md)) is a **directory attribute**, not a claims integration: no
  member verification, no billing codes, in the MVP.
- **Not a triage or telehealth product.** The optional comment field on a queue ticket
  ([04](04-display-monitor.md)) is a short **free-text reason for visit** for staff convenience, not a
  clinical triage score, and is never a substitute for a nurse assessing an emergency at the door.

## Actors

| Actor | Role |
|-------|------|
| Patient / member of the public | Searches for a clinic, joins the queue (app/USSD/WhatsApp/web), gets notified, arrives near their turn |
| Guest / walk-in patient | Arrives in person with no phone/app use; reception issues a ticket on their behalf |
| Receptionist / clerk | Manages the desk queue, calls next, marks arrived/no-show, issues walk-in tickets |
| Nurse / doctor | Sees the live queue for their room, calls the next patient, adds a short visit note |
| Clinic manager / admin | Configures clinic profile (public/private, hours, accepted payment types), views reports |
| Platform operator (ClinicQ) | Runs the platform, onboards clinics, support, billing |
| Medical aid / payment metadata (future) | Directory-only attribute today; integration is a later phase, see [02](02-discovery-and-geolocation.md) |
| Platform | ClinicQ software (discovery, queueing, dashboard, display, notifications) |

## Software modules

1. **Clinic discovery** - geo-location search, distance/ETA, public/private toggle, live queue length per
   clinic, later: filter by accepted medical aid / cash / card (private clinics only)
2. **Booking & digital queue** - join remotely (get a ticket before leaving home) or walk in; ticket
   number, position, live estimated wait
3. **Multi-channel access** - mobile PWA, USSD menu, WhatsApp bot, web app - same booking/queue engine
   behind all four
4. **Notifications** - "you're #5", "you're next", "please come in now" via push/SMS/WhatsApp
5. **Clinic dashboard** - receptionist/nurse view: call next, skip/recall, walk-in intake, per-room queues
6. **Display monitor** - waiting-room screen: ticket number, called time, patient name/initials (clinic
   choice), optional short comment (e.g. "headache", "follow-up", "stomach ache")
7. **Reporting & analytics** - average wait time, no-show rate, peak hours, queue-length trend
8. **Admin & security** - RBAC, POPIA consent, audit logs, clinic profile management

## Stack (chosen)

**Python 3.14 - FastAPI - PostgreSQL 18+ with PostGIS - htmx + Jinja2 (server-rendered PWA) - Redis -
Docker.**

One Python codebase renders the patient-facing discovery/queue pages, the clinic dashboard, and the
display-monitor signage page: FastAPI serves Jinja2 templates and htmx swaps fragments in place (queue
position ticks down live, the dashboard's call-next button updates the display screen) without a
separate JS build. PostGIS powers the "clinics near me" geo-search
([02](02-discovery-and-geolocation.md)). USSD and WhatsApp are additional **channels** into the same
booking/queue API, not separate systems; see [06](06-channels-app-ussd-whatsapp-web.md) and
[13-tech-implementation.md](13-tech-implementation.md).

## Quick answers

| Question | Short answer |
|----------|--------------|
| Where does the name come from? | **ClinicQ:** clinic + queue. Shortlisted as "ClinicQueue"; shortened for the wordmark |
| What's new vs the original idea? | Same appointment/queue core, **plus** geo-location clinic discovery with a public/private toggle, and multi-channel access (app, USSD, WhatsApp, web) - not just one app |
| How does geo-location work? | PostGIS radius/distance query against clinic locations; patient's phone GPS (or manually entered address/area) - see [02](02-discovery-and-geolocation.md) |
| What does public/private toggle do? | Filters the clinic list to government/public clinics, private clinics, or both - **payment filters (medical aid / cash / card) only ever apply under "private"** |
| Is the medical-aid filter live on day one? | **No** - it's a Phase 2 enhancement once a clinic's accepted-payment metadata is captured; MVP ships public/private only - see [02](02-discovery-and-geolocation.md) |
| What shows on the waiting-room screen? | Ticket number + called time always; name/initials and comment are **clinic-configurable** (privacy) - see [04](04-display-monitor.md) |
| App needed? | **PWA** for patients/clinics is enough; **USSD** covers feature phones/no-data; **WhatsApp** covers the channel most South Africans already use daily - see [06](06-channels-app-ussd-whatsapp-web.md) |
| Devices needed per clinic? | A monitor/TV + small signage box, reception PC/tablet - see [07-devices-and-bom.md](07-devices-and-bom.md) |
| Startup cash for one pilot clinic? | **~R6,000-15,000 (~$330-830)** - far less than ElimuKadi/UmojaNet since there's no card stock or radio gear - see [09-pricing-and-budget.md](09-pricing-and-budget.md) |
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
mermaid.js, the same source as the fenced ```mermaid blocks in the numbered docs, which GitHub also renders
natively.

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
