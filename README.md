<h1 align="center">ClinicQ</h1>

<p align="center">
  <strong>Find a clinic. Hold your place. Watch your number come up.</strong><br>
  Clinic discovery and digital queue management for public and private clinics: from a phone, a USSD
  menu, WhatsApp, or the web.
</p>

<p align="center">
  <a href="docs/PLAN/IMPLEMENTATION_PLAN.md">Implementation plan</a> ·
  <a href="docs/GITHUB/README.md">Milestones &amp; issues</a> ·
  <a href="docs/TEAM/WORKLOAD_SPLIT.md">Workload split</a> ·
  <a href="docs/PRODUCT/README.md">Product docs</a> ·
  <a href="https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/DEMO/index.html">Visual demo</a>
</p>

---

**Richfield Graduate Institute of Technology** · BSc IT (Distance Learning) · **Capstone Project 2026**

## The problem

You take a morning off work, travel to your nearest clinic, and join a queue with no idea whether the
wait is twenty minutes or five hours. Nobody can tell you. There is no number, no board, no message:
just a bench and a paper list. Digital queue management has existed abroad for over a decade; most
South African clinics, especially public ones, still run paper-and-shout queues.

## What ClinicQ does

Find a nearby clinic, public or private, by geolocation, join its digital queue **before you leave
home**, and watch your ticket number count down on the clinic's own waiting-room display, while staff
run the whole line from one dashboard.

```text
        +------------------------- ClinicQ queue engine -------------------------+
        |                                                                        |
  Mobile PWA ---------|                                                          |
  USSD menu ----------> JOIN QUEUE (ticket number, live position, ETA)           |
  WhatsApp bot -------|                                                          |
  Walk-in (reception) |                                                          |
        |                                                                        |
  Clinic dashboard --> CALL NEXT --> patient's phone + waiting-room display      |
        +------------------------------------------------------------------------+
            Same queue, same ticket number, no matter which door you came in
```

## Features

### For patients

| | Feature |
|---|---|
| 📍 | **Find clinics near you:** PostGIS distance search, filtered by public, private or both, with live queue length and an honest wait range |
| 🎟️ | **Join from anywhere:** a ticket number and live position before you leave home |
| 📱 | **Four ways in:** mobile PWA, **USSD on any phone with no data**, WhatsApp bot, or plain web |
| 🔔 | **Get told when it's your turn:** push, SMS or WhatsApp saying "you're #5", "you're next", "come in now" |
| 🗓️ | **Book an appointment:** scheduled slots that turn into a queue ticket automatically |
| 🧾 | **Check in at the door:** scan a QR at the kiosk instead of queueing to talk to reception |
| 👨‍👩‍👧 | **Book for someone else:** a parent for a child, a daughter for her mother |
| 💊 | **Chronic medication reminders:** a nudge and a one-tap join for your collection |
| 🚗 | **Virtual waiting room:** wait nearby; we call you forward accounting for your travel time |
| 🌍 | **Five languages:** English, isiZulu, isiXhosa, Afrikaans and Sesotho, across menus, messages and announcements |

### For clinic staff

| | Feature |
|---|---|
| 🖥️ | **One dashboard:** every active queue side by side, live, with one enormous *Call Next* |
| 🚶 | **Walk-in intake in under 10 seconds:** same sequence as remote joins, one fair queue |
| 🏥 | **Multi-room queues:** triage → doctor → pharmacy, with transfers that don't send patients to the back |
| ⚖️ | **Clinical priority overrides:** two taps, a reason code, and a full audit trail |
| 📺 | **Waiting-room display board:** number, time, and privacy-gated name or comment, with an audio call-out |
| 🔒 | **Privacy by default:** every new clinic starts number-only; names and reasons are opt-in and audited |
| ⚙️ | **Run your own clinic:** hours, holidays, queues, services, staff, display mode, all self-service |
| 📊 | **Reports that sell themselves:** wait times, no-show rate, channel mix, busiest-hour heatmap |

### For the platform

RBAC across five roles · hard multi-tenant isolation · append-only, tamper-evident audit log · POPIA
consent capture and withdrawal · automatic data retention and purge · data-subject access and erasure ·
anonymised district-level dashboards for health-department planning.

## Tech stack

| Layer | Choice |
|-------|--------|
| Language | **Python 3.14** |
| API + web | **FastAPI** (ASGI) with **Jinja2** templates |
| Interactivity | **htmx** + a little **Alpine.js** + **SSE**, no separate JS build |
| Styling | **Tailwind CSS** (compiled) |
| Database | **PostgreSQL 18** with **PostGIS** |
| ORM / migrations | **SQLAlchemy 2.x (async)** + **Alembic** |
| Cache / jobs | **Redis** + `arq` |
| Channels | USSD gateway (Africa's Talking-class) · WhatsApp Business Cloud API · SMS · Web Push |
| Packaging / infra | **uv** · **Docker Compose** · **GitHub Actions** |

One Python codebase renders the patient pages, the clinic dashboard and the display board. For a
six-person team that is the difference between one thing to debug and three, and the `/api/*` surface
is there from day one, which is exactly what the USSD and WhatsApp adapters consume.

## Project documentation

| Document | What's in it |
|----------|--------------|
| **[Implementation plan](docs/PLAN/IMPLEMENTATION_PLAN.md)** | The whole project on one page: architecture, sequence, features added beyond the brief, how we'll know it works |
| **[Milestones & issues](docs/GITHUB/README.md)** | 14 milestones, 109 tracked issues, conventions, release tags, pipeline strategy |
| **[Workload split](docs/TEAM/WORKLOAD_SPLIT.md)** | Six roles, sprint-by-sprint lanes, **who blocks whom and what to do about it**, risk register |
| **[Engineering non-negotiables](docs/guideline.md)** | Five rules enforced by guard tests |
| **[Product docs](docs/PRODUCT/README.md)** | 14 numbered docs: discovery, queue, display, dashboard, channels, devices, topology, pricing, business plan, marketing, upscaling, tech, benchmark |
| **[Visual demo](docs/DEMO/index.html)** | A one-page walkthrough of the finished product, for the team and the showcase |

## Delivery at a glance

| | Milestone | Issues | Sprints | Tag |
|---|-----------|--------|---------|-----|
| 1 | [Foundation & Local CI](docs/GITHUB/MILESTONES/M1_foundation_local_ci.md) | 1–8 | 1–2 | `v0.1.0` |
| 2 | [CI/CD & Team Workflow](docs/GITHUB/MILESTONES/M2_cicd_environments.md) | 9–14 | 2 | `v0.2.0` |
| 3 | [Identity, Auth, RBAC & Consent](docs/GITHUB/MILESTONES/M3_identity_auth_rbac.md) | 15–22 | 3–4 | `v0.3.0` |
| 4 | [Clinics, Queues & Configuration](docs/GITHUB/MILESTONES/M4_clinics_queues_config.md) | 23–30 | 4–5 | `v0.4.0` |
| 5 | [Discovery & Geolocation](docs/GITHUB/MILESTONES/M5_discovery_geolocation.md) | 31–38 | 5–6 | `v0.5.0` |
| 6 | [**Queue Engine Core**](docs/GITHUB/MILESTONES/M6_queue_engine_core.md) ⚠️ critical path | 39–47 | 6–7 | `v0.6.0` |
| 7 | [Clinic Dashboard](docs/GITHUB/MILESTONES/M7_clinic_dashboard.md) | 48–55 | 8–9 | `v0.7.0` |
| 8 | [Display Monitor](docs/GITHUB/MILESTONES/M8_display_monitor.md) | 56–62 | 9 | `v0.8.0` |
| 9 | [Notifications & Patient PWA](docs/GITHUB/MILESTONES/M9_notifications_patient_pwa.md) | 63–71 | 9–10 | `v0.9.0` |
| 10 | [USSD & WhatsApp Channels](docs/GITHUB/MILESTONES/M10_ussd_whatsapp_channels.md) | 72–79 | 10–11 | `v0.10.0` |
| 11 | [Appointments & Check-in](docs/GITHUB/MILESTONES/M11_appointments_checkin_patient_care.md) | 80–87 | 11–12 | `v0.11.0` |
| 12 | [Reporting & Analytics](docs/GITHUB/MILESTONES/M12_reporting_analytics.md) | 88–94 | 12 | `v0.12.0` |
| 13 | [Security, Privacy & POPIA](docs/GITHUB/MILESTONES/M13_security_privacy_compliance.md) | 95–101 | 12–13 | `v0.13.0` |
| 14 | [Production, Pilot & Go-live](docs/GITHUB/MILESTONES/M14_production_pilot_golive.md) | 102–109 | 13–14 | `v0.14.0` |
| ⭐ | **First official release:** every issue 1–109 closed | - | end of 14 | **`v1.0.0`** |

## Team

| Code | Role | Name | GitHub | Owns |
|---|------|------|--------|------|
| A | Backend Lead | | | Domain core, **queue engine**, appointments |
| B | Backend / Integrations | | | Notifications, SMS, WhatsApp, USSD, i18n |
| C | Frontend / Patient | | | Discovery, map, ticket page, PWA, kiosk, accessibility |
| D | Frontend / Clinic | | | Dashboard, waiting-room display board |
| E | DevOps / QA Lead | | | Repo, CI/CD, security, production, pilot rollout |
| F | Data & Research Lead | | | Analytics, compliance, translations, UAT, capstone deliverables |

Each role has a **named backup** who reviews their PRs and picks up their work if they are unavailable;
see the [workload split](docs/TEAM/WORKLOAD_SPLIT.md#1-the-six-roles).

## Getting started

> The application code is not written yet; M1 creates it. These are the commands the repository will
> support from the end of sprint 1.

```bash
uv sync
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d db redis
uv run alembic upgrade head
uv run scripts/seed_dev_data.py
uv run uvicorn app.main:app --reload --port 8000
```

Then open:

- `http://localhost:8000/discover`: find a clinic
- `http://localhost:8000/dashboard/1`: clinic dashboard
- `http://localhost:8000/display/1`: waiting-room board

Before every push:

```bash
./scripts/ci-local.sh
```

## Contributing (team workflow)

- Branch: `Issue/<N>/<short-slug>`, e.g. `Issue/39/tickets-model-sequence`
- Commit: `Issue 39: add concurrency-safe ticket sequence`
- PR description in `docs/GITHUB/PR/M<MS>/PR_<N>_DESCRIPTION.md`, ending with `Closes #N`
- One issue, one PR, merged within 3 days. Rebase daily.
- Review within 24 hours on a weekday, or the backup reviewer may merge.

Full detail: [engineering non-negotiables](docs/guideline.md) and
[workload split](docs/TEAM/WORKLOAD_SPLIT.md).

## Status

**Phase:** planning complete. Implementation begins at M1.

- [x] Idea finalised (**ClinicQ**, selected from the shortlist)
- [x] Requirements gathered ([product docs](docs/PRODUCT/README.md))
- [x] Architecture agreed ([implementation plan](docs/PLAN/IMPLEMENTATION_PLAN.md))
- [x] Milestones and issues defined (14 milestones, 109 issues)
- [x] Workload split and dependency analysis ([workload split](docs/TEAM/WORKLOAD_SPLIT.md))
- [ ] Repo structure set up (M1)
- [ ] CI/CD pipeline running (M2)
- [ ] MVP complete (M8)
- [ ] Pilot clinic live (M14)
- [ ] Demo-ready

---

## Project shortlist (history)

This repository began as the team's capstone shortlist. **ClinicQ**, shortlisted as *ClinicQueue*, was
selected. The other five ideas are kept for reference in
[`docs/PROJECTS/`](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md):
CivicConnect, EduAttend, KasiMarket, CommunityNet, IsangoPass.

## Licence

See [LICENSE](LICENSE).
