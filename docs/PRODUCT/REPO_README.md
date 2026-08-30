# clinicq: repository blueprint

> **Superseded, kept for reference.** This file was written before the repository existed, as a preview
> of what its `README.md` should look like. The real one now lives at the
> [repository root](../../README.md). What is still useful here is the **intended source layout**, the
> **environment variables** and the **first-deployment steps**; the rest has been overtaken by
> [`docs/PLAN/IMPLEMENTATION_PLAN.md`](../PLAN/IMPLEMENTATION_PLAN.md) and
> [`docs/GITHUB/`](../GITHUB/README.md).

**ClinicQ:** clinic discovery + digital queue platform. Find a nearby public or private clinic by
geolocation, join its queue from a phone (app, USSD, WhatsApp) or the web before arriving, and watch a
ticket number count down on the clinic's own waiting-room display while staff run the line from one
dashboard.

Full product, business and engineering docs live in [`docs/PRODUCT/`](README.md).

## Suggested repo name

| Option | Notes |
|--------|-------|
| **`clinicq`** (recommended) | Matches the brand exactly; short, lowercase, unambiguous |
| `clinic-q` | If a hyphen reads better in URLs and package names |
| `clinicq-platform` | If `clinicq` is reserved for a monorepo umbrella and services are split later |

This document assumes **`clinicq`**.

## What this repository will contain

A single Python service that renders the patient discovery/queue pages, the clinic dashboard, and the
waiting-room display-monitor board, plus two background workers (notifications, daily stats
aggregation) and thin channel adapters for USSD and WhatsApp. See
[`13-tech-implementation.md`](13-tech-implementation.md) for the full design.

```text
clinicq/
  app/
    main.py             # FastAPI app factory, routers mounted here
    api/                # JSON endpoints (/api/*) - discovery, queue, webhooks
    web/                # HTML routes rendered with Jinja2 + htmx fragments
      discover/         # Clinic search/discovery pages (public/private toggle, map/list)
      queue/            # Patient ticket/position pages
      dashboard/        # Receptionist/nurse/manager dashboard pages
      display/          # Waiting-room display-monitor board page (kiosk mode)
    models/             # SQLAlchemy models (sites, queues, tickets, staff, patients)
    schemas/            # Pydantic schemas
    services/           # Business logic: geo-search, queue/ticket rules, notifications
    templates/          # Jinja2 templates (base layout, discover, queue, dashboard, display, fragments)
    static/             # Tailwind output CSS, htmx.min.js, manifest.json, service-worker.js, icons
  channels/
    ussd/               # USSD gateway webhook adapter + session-state handling
    whatsapp/           # WhatsApp Business API webhook adapter + quick-reply flows
  workers/
    notifications_worker.py  # SMS/push/WhatsApp queue consumer
    stats_worker.py          # Nightly daily_queue_stats aggregation
  migrations/           # Alembic versions
  infra/
    docker-compose.yml
    grafana-dashboards/ (optional)
  tests/
  scripts/
    simulate_ticket.py   # local dev: fake a patient joining a queue
    simulate_ussd.py     # local dev: fake a USSD session step
  pyproject.toml
  .env.example
  README.md
```

## Tech stack

| Layer | Choice |
|-------|--------|
| Language | **Python 3.14** |
| Web framework | **FastAPI** (ASGI, Uvicorn) |
| Templates / interactivity | **Jinja2** + **htmx** (+ small Alpine.js where needed) |
| Styling | **Tailwind CSS** (compiled, no runtime JS framework) |
| Database | **PostgreSQL 18+** with **PostGIS** |
| ORM / migrations | **SQLAlchemy 2.x (async)** + **Alembic** |
| Cache / queues | **Redis** + `arq` (async task queue) |
| USSD | Africa's Talking-class gateway webhook |
| WhatsApp | WhatsApp Business Cloud API (Meta) or a BSP (Twilio/360dialog-class) |
| SMS | Regional SMS gateway (notification fallback) |
| Package management | **uv** |
| Containers | **Docker Compose** |
| CI | **GitHub Actions** (lint with ruff, type-check, pytest) |

Full rationale in [`13-tech-implementation.md`](13-tech-implementation.md).

## Getting started (once the repo exists)

```bash
# Prerequisites: Python 3.14, uv, Docker

git clone <repo-url> clinicq
cd clinicq

uv sync                        # install dependencies from pyproject.toml
cp .env.example .env           # fill in DB, Redis, USSD/WhatsApp sandbox keys

docker compose up -d db redis      # infra only (Postgres w/ PostGIS extension), API runs locally for fast iteration
uv run alembic upgrade head        # apply migrations
uv run scripts/seed_dev_data.py    # a few test clinics with real-looking coordinates, queues

uv run uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/discover` for clinic search, `http://localhost:8000/dashboard/1` for the
clinic dashboard, and `http://localhost:8000/display/1` for the waiting-room board (seeded admin
credentials printed by the seed script).

Simulate a queue join and a USSD session without real gateway hardware/credentials:

```bash
uv run scripts/simulate_ticket.py --site-id 1 --source app
uv run scripts/simulate_ussd.py --session-id TESTSESSION01 --input "1"
```

This should add a ticket to the seeded clinic's queue and flip the display board and dashboard within a
few seconds, proving the join -> ticket -> dashboard/display loop before wiring up real USSD/WhatsApp
gateway credentials.

## Environment variables (`.env.example` contents)

```env
DATABASE_URL=postgresql+asyncpg://clinicq:clinicq@localhost:5432/clinicq
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=change-me
JWT_ALGORITHM=HS256

USSD_GATEWAY_PROVIDER=africastalking
USSD_GATEWAY_API_KEY=

WHATSAPP_BUSINESS_API_TOKEN=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=

SMS_GATEWAY_API_KEY=

DEFAULT_TIMEZONE=Africa/Johannesburg
```

## Deployment (first real clinic)

1. Provision a small cloud VPS - see
   [`13-tech-implementation.md`](13-tech-implementation.md#9-hosting-choice).
2. `docker compose -f infra/docker-compose.yml up -d` - brings up Postgres/PostGIS, Redis, the API, and
   both workers.
3. Set up the display box (Raspberry Pi or a reused PC) at the clinic in a kiosk-mode browser pointed at
   `/display/{site_id}`.
4. Verify the dashboard and display board on the clinic's real internet connection and devices before
   onboarding real patients.
5. Switch on USSD/WhatsApp channels only after the PWA/web remote-join flow has been tested for a few
   days.

## Roadmap

Superseded by the full plan: **14 milestones, 109 issues, 28 weeks**;
see [`docs/PLAN/IMPLEMENTATION_PLAN.md`](../PLAN/IMPLEMENTATION_PLAN.md) and
[`docs/GITHUB/README.md`](../GITHUB/README.md).

## Related docs

- [`docs/PRODUCT/README.md`](README.md) - full doc index (naming, discovery, queue, display,
  dashboard, channels, devices, topology, pricing, business plan, marketing, 24-month projection, tech
  implementation, benchmark)
- [`docs/PRODUCT/index.html`](index.html) - tabbed HTML viewer with Mermaid diagrams
- [`docs/PROJECTS/index.html#clinic-queue`](../PROJECTS/index.html#clinic-queue) - original
  capstone shortlist entry (as *ClinicQueue*)

## License

Not yet decided - pick one (e.g. MIT or Apache-2.0) when the repository is actually created.
